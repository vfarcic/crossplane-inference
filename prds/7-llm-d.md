# PRD #7: Switch from vLLM Production Stack to llm-d

**Status**: Discussion
**Priority**: High
**Created**: 2026-02-19
**Updated**: 2026-03-30
**GitHub Issue**: [#7](https://github.com/vfarcic/crossplane-inference/issues/7)

## Problem Statement

The current `LLMInference` composition uses the vLLM Production Stack operator (`VLLMRuntime` CRD) to deploy models. This operator has a fundamental limitation: it continuously reconciles `Deployment.spec.replicas` from `deploymentConfig.replicas`, making it impossible to use any external autoscaler (KEDA, HPA). This was the blocking issue that [deferred the Gateway + KEDA PRD](gateway-api-keda-inference.md).

Beyond autoscaling, the operator lacks:
- **Inference-aware routing** — traffic goes through plain Ingress (round-robin), ignoring KV-cache state, loaded LoRA adapters, and pod saturation
- **Disaggregated serving** — prefill and decode phases run on the same GPU pool, causing interference at scale
- **Multi-accelerator support** — hardcoded to NVIDIA with a specific image fork (`lmcache/vllm-openai`)

[llm-d](https://llm-d.ai/) (CNCF Sandbox, March 2026) solves all of these by providing a Kubernetes-native distributed inference framework built on vLLM with integrated routing, autoscaling, and disaggregated serving.

## Context

### Why llm-d?

llm-d is the convergence point for Kubernetes-native LLM inference. Backed by Red Hat, Google Cloud, IBM, CoreWeave, and NVIDIA, it integrates:

- **vLLM** as the inference engine (same engine dot-inference already uses)
- **Gateway API Inference Extension** for KV-cache-aware, prefix-aware, LoRA-aware routing
- **Variant Autoscaler** for traffic-and-hardware-aware scaling (no operator conflicts)
- **NIXL** for GPU-to-GPU KV-cache transfer enabling disaggregated prefill/decode
- **Multi-accelerator images** — NVIDIA, AMD, Intel Gaudi, CPU-only

### llm-d Architecture

llm-d uses Helm charts, not a CRD operator:

| Component | Scope | Source |
|-----------|-------|--------|
| `llm-d-infra` | Cluster-level | Gateway setup (Istio/Envoy/GKE) |
| `llm-d-modelservice` | Per-model | Deployments, routing sidecars, init containers |
| `inferencepool` (GAIE) | Per-model | InferencePool + EPP (Endpoint Picker) |
| Variant autoscaler | Cluster-level | Traffic-aware autoscaling controller |

Since dot-kubernetes already installs Envoy AI Gateway (which is GAIE-compatible), we skip `llm-d-infra` and use the existing gateway infrastructure.

### What Changes

| Concern | Current | With llm-d |
|---------|---------|------------|
| Model deployment | `VLLMRuntime` CRD → operator → Deployment | `llm-d-modelservice` Helm release → Deployment directly |
| Routing | Ingress (round-robin) | InferencePool + EPP + HTTPRoute (inference-aware) |
| Autoscaling | Impossible (operator fights KEDA) | Variant autoscaler (traffic-aware, no conflicts) |
| Image | `lmcache/vllm-openai:v0.3.13` (single fork) | `ghcr.io/llm-d/llm-d-cuda:v0.5.1` (multi-accelerator) |
| Service port | 80 (operator convention) | 8000 (vLLM native) |

### Deferred PRD Superseded

This PRD supersedes the [Gateway + KEDA PRD](gateway-api-keda-inference.md) (deferred). That PRD attempted to bolt Gateway API routing and KEDA autoscaling onto the VLLMRuntime operator — llm-d provides both natively. The API design from that PRD (host, criticality, minReplicas, maxReplicas, scaling) remains relevant and is adopted here.

## Proposed API Surface

### Minimal Change (Phase 1)

Replace the backend while keeping the same user-facing API:

```yaml
apiVersion: inference.devopstoolkit.ai/v1alpha1
kind: LLMInference
metadata:
  name: qwen
  namespace: dev
spec:
  model: Qwen/Qwen2.5-3B-Instruct
  gpu: 1
  host: qwen.example.com           # was: ingressHost
  providerConfigName: inference-small
  targetNamespace: dev
```

- `ingressHost` → `host` (generates HTTPRoute + InferencePool instead of Ingress)
- When `host` is omitted, no routing resources are generated (same behavior as before)
- `model`, `gpu`, `providerConfigName`, `targetNamespace` remain unchanged

### Extended API (Phase 2)

Add scaling and priority fields (adopted from the deferred PRD design):

```yaml
spec:
  model: meta-llama/Llama-3.3-70B-Instruct
  gpu: 8
  host: llama.example.com
  providerConfigName: inference-large
  criticality: critical         # critical (default) | sheddable
  minReplicas: 1                # default: 1 (set 0 for scale-to-zero)
  maxReplicas: 4                # default: 3
```

### Disaggregated Serving (Phase 3)

For large models that benefit from prefill/decode separation:

```yaml
spec:
  model: meta-llama/Llama-3.3-70B-Instruct
  gpu: 8
  host: llama.example.com
  providerConfigName: inference-large
  prefill:
    replicas: 1                 # 0 = disabled (default), colocated serving
    gpu: 8                      # can differ from decode GPU count
```

### Full Example

```yaml
apiVersion: inference.devopstoolkit.ai/v1alpha1
kind: LLMInference
metadata:
  name: llama-3
  namespace: production
spec:
  model: meta-llama/Llama-3.3-70B-Instruct
  gpu: 8
  host: llama.example.com
  providerConfigName: inference-large
  targetNamespace: production
  criticality: critical
  minReplicas: 2
  maxReplicas: 8
  prefill:
    replicas: 1
    gpu: 8
```

## Composition Design

### Provider Strategy: Hybrid

- **`provider-helm`** for `llm-d-modelservice` — the Helm chart handles complex Deployment templating (sidecars, init containers, volume mounts, accelerator-specific config). Replicating this in Python would be fragile.
- **`provider-kubernetes`** for Gateway API resources (InferencePool, HTTPRoute, InferenceObjective, EPP) — these are simple, stable CRs.

### Generated Resources

**Always generated:**
1. `Release` (provider-helm) — `llm-d-modelservice` Helm release with values mapped from XR spec

**When `host` is set:**
2. `Object` wrapping `InferencePool` (v1) — targets model pods by label, references EPP
3. `Object` wrapping `HTTPRoute` — routes from Gateway to InferencePool
4. `Object` wrapping `InferenceObjective` (v1alpha2) — priority from criticality
5. `Object` wrapping EPP `Deployment` + `Service` + RBAC (or use the `inferencepool` Helm chart via a second `Release`)

**When scaling fields are set:**
6. Variant autoscaler configuration (exact resource TBD — depends on autoscaler API)

### Helm Values Mapping

```python
# XR spec → llm-d-modelservice Helm values
values = {
    "modelArtifacts": {
        "name": model,              # from spec.model
        "uri": f"hf://{model}",
    },
    "decode": {
        "replicas": min_replicas,   # from spec.minReplicas
        "parallelism": {
            "tensor": gpu if gpu > 1 else 1,
        },
        "containers": [{
            "name": "vllm",
            "image": "ghcr.io/llm-d/llm-d-cuda:v0.5.1",
            "modelCommand": "vllmServe",
        }],
    },
    "accelerator": {
        "type": "nvidia",
    },
    "routing": {
        "servicePort": 8000,
    },
}

# Disaggregated serving (Phase 3)
if prefill_replicas > 0:
    values["prefill"] = {
        "replicas": prefill_replicas,
        "parallelism": {"tensor": prefill_gpu},
    }
```

## Implementation Approach

### Phase 1: Replace VLLMRuntime with llm-d-modelservice

- [ ] Add `provider-helm` dependency to `crossplane.yaml`
- [ ] Replace VLLMRuntime Object generation with `llm-d-modelservice` HelmRelease
- [ ] Map existing XR fields (model, gpu) to Helm values
- [ ] Rename `ingressHost` → `host` in XRD (breaking change, major version bump)
- [ ] Remove Ingress generation
- [ ] Update GPU heuristics for llm-d (CPU/memory derivation may differ)
- [ ] Update tests: assert HelmRelease instead of VLLMRuntime Object
- [ ] Update examples

### Phase 2: Gateway API routing + scaling

- [ ] Generate InferencePool + EPP when `host` is set
- [ ] Generate HTTPRoute (parentRef to Gateway, backendRef to InferencePool)
- [ ] Generate InferenceObjective (criticality → priority mapping)
- [ ] Add `criticality`, `minReplicas`, `maxReplicas` to XRD
- [ ] Integrate variant autoscaler (exact mechanism TBD after evaluating autoscaler API)
- [ ] Tests for routing resources and scaling config

### Phase 3: Disaggregated serving

- [ ] Add `prefill` section to XRD
- [ ] Configure llm-d-modelservice Helm values for P/D disaggregation
- [ ] Routing sidecar configuration for NIXL KV-cache transfer
- [ ] Tests for disaggregated deployment topology

### Phase 4: Guides and validation

- [ ] Update Google Cloud guide for llm-d
- [ ] Update AWS guide for llm-d
- [ ] Write Azure guide
- [ ] Validate end-to-end on real GPU clusters

## Testing

### Test Environment

Extend existing KinD cluster:
- Install Gateway API + Inference Extension CRDs (already present)
- Add `llm-d-modelservice` Helm chart availability (chart repo or local)
- Use `llm-d-inference-sim` (GPU-free simulator) for functional testing without GPUs

### Test Cases

- **Basic model deployment** — HelmRelease generated with correct values
- **GPU heuristics** — 1 GPU vs 8 GPU produces correct parallelism and resource config
- **No host** — HelmRelease generated, no routing resources
- **With host** — InferencePool + HTTPRoute + InferenceObjective + EPP generated
- **Criticality** — critical (priority 100) vs sheddable (priority 0)
- **Custom replicas** — minReplicas correctly sets decode replicas
- **Disaggregated** — prefill.replicas > 0 generates separate prefill config
- **Target namespace** — all resources land in correct namespace

## Dependencies

- **Upstream**: [crossplane-kubernetes #276](https://github.com/vfarcic/crossplane-kubernetes/issues/276) — Install llm-d variant autoscaler on target clusters
- **Upstream**: dot-kubernetes already provides Envoy AI Gateway + GAIE CRDs (v2.0.18)
- **New provider**: `provider-helm` added as a dependency alongside existing `provider-kubernetes`

## Related PRDs

- [PRD #9: Gateway Routing and KEDA Autoscaling](gateway-api-keda-inference.md) — **Superseded** by this PRD. API design adopted, implementation approach replaced.
- [PRD #2: Model Caching with PVs](2-model-caching.md) — Model caching complements llm-d (faster cold starts, viable scale-to-zero)
- [PRD #5: KV-Cache Routing](5-kv-cache.md) — Covered by llm-d's inference scheduler and NIXL-based KV-cache transfer
- [PRD #6: Multi-Cluster Inference](6-multi-cluster.md) — Multi-cluster routing builds on Gateway API foundation from this PRD

## Open Questions

- Does `llm-d-modelservice` Helm chart work with Envoy AI Gateway, or does it assume Istio? Need to validate.
- What is the exact API for the variant autoscaler? Need to inspect the controller CRDs/config.
- Should we use the `inferencepool` upstream Helm chart for EPP, or generate the resources directly?
- Is `provider-helm` the right approach, or should we template the Deployment resources directly for more control?
- How does model artifact download work in llm-d? VLLMRuntime handled this via the operator; llm-d uses init containers. Do we need to expose `modelArtifacts.authSecretName` in the XRD for private models?

## Decision Log

| Date | Decision | Rationale | Impact |
|------|----------|-----------|--------|
| 2026-02-19 | Created as discussion PRD for disaggregated inference | llm-d was early (v0.4), exploration only | Low priority, no implementation |
| 2026-03-30 | Elevated to full replacement PRD | llm-d reached v0.5.1, CNCF Sandbox, solves both autoscaling and routing blockers that deferred PRD #9. vLLM Production Stack operator has no path to fixing the KEDA conflict. | Supersedes PRD #9. Priority raised to High. Scope expanded from "optional disaggregated serving" to "full backend replacement" |
| 2026-03-30 | Hybrid provider strategy (provider-helm + provider-kubernetes) | llm-d-modelservice Helm chart handles complex deployment topology (sidecars, init containers, multi-container pods). Replicating in Python would be fragile and lag behind releases. Gateway API resources are simple enough for provider-kubernetes. | Adds provider-helm dependency. Composition generates HelmRelease instead of raw Objects for model serving. |
| 2026-03-30 | Adopt API design from deferred PRD #9 | The host, criticality, minReplicas, maxReplicas fields were well-designed and tested (all e2e tests passed before deferral). No reason to redesign. | Continuity with previous work. Users who previewed the PRD #9 API get the same interface. |
| 2026-03-30 | Feature request to crossplane-kubernetes for llm-d infra | Variant autoscaler is a cluster-level controller, belongs in dot-kubernetes. Filed as [crossplane-kubernetes #276](https://github.com/vfarcic/crossplane-kubernetes/issues/276). | Dependency on crossplane-kubernetes for Phase 2 scaling support. Phase 1 (model deployment + routing) can proceed independently. |

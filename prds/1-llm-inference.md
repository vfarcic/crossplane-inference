# PRD #1: LLM Inference on Kubernetes with vLLM and Crossplane

**Status**: In Progress
**Priority**: High
**Created**: 2026-02-16
**GitHub Issue**: [#1](https://github.com/vfarcic/crossplane-inference/issues/1)

## Problem Statement

Running LLM inference on Kubernetes requires significant boilerplate: GPU resource management, shared memory volumes for tensor parallelism, model-specific CLI arguments, health probes tuned to model load times, and HuggingFace token handling for gated models. Each new model deployment repeats this work with slight variations.

There is no simple, unified API to provision inference workloads across different model sizes (1.5B to 1T parameters), types (generative and embedding), and hardware requirements (1 GPU to 8 GPUs with tensor parallelism).

## Solution Overview

**Phase 1:** Plain Kubernetes YAML manifests for three vLLM scenarios, validated directly with `kubectl apply`.

**Phase 2:** A Crossplane Configuration (`dot-inference`) with an inline Python composition function (via `function-python` engine), published to the Upbound Marketplace. The composition generates `provider-kubernetes` Object resources wrapping a `VLLMRuntime` CR and an Ingress, deployed to a user-specified target cluster. Users specify only what matters — model name, GPU count, ingress host, and target cluster ProviderConfig — via a single `LLMInference` custom resource at `inference.devopstoolkit.ai/v1alpha1`. All infrastructure complexity (shared memory, tensor parallelism, health probes, worker multiproc, HuggingFace tokens) is handled internally by the composition function based on the inputs.

All scenarios use **vLLM** as the inference engine, with the [vLLM Production Stack operator](https://github.com/vllm-project/production-stack) managing runtime lifecycle in Phase 2.

## User Journey

### Without this feature:
1. Research vLLM container images, CLI args, and health endpoints
2. Manually write Deployment YAML with GPU resources, /dev/shm volumes, probes
3. Debug tensor parallelism configuration for multi-GPU models
4. Repeat for each model/environment

### With this feature:
1. Apply a plain manifest OR create a Crossplane XR with model name and GPU count
2. Inference endpoint is ready

**Crossplane XR example:**
```yaml
apiVersion: inference.devopstoolkit.ai/v1alpha1
kind: LLMInference
metadata:
  name: my-llm
spec:
  model: "Qwen/Qwen2.5-1.5B-Instruct"
  gpu: 1
  ingressHost: "qwen.127.0.0.1.nip.io"
  providerConfigName: "gpu-cluster"
```
That's it. Four fields. The composition function handles everything else — compute sizing, shared memory, tensor parallelism, health probes, env vars, and Ingress routing.

## Scope

### In Scope
- Plain K8s manifests for 3 vLLM deployments (Qwen2.5-1.5B, bge-base-en-v1.5, Kimi K2.5)
- Ingress resources for external access using nip.io domains (e.g., `qwen.127.0.0.1.nip.io`)
- Inline Python composition function via `function-python` engine
- Crossplane XRD defining `LLMInference` API (namespace-scoped, Crossplane v2)
- Crossplane Composition with embedded Python script
- Configuration package manifest for Upbound Marketplace
- Chainsaw e2e tests against KinD (no cloud provider costs)
- Example XRs for each scenario
- KinD cluster as Crossplane control plane for testing
- Documentation of dot-kubernetes integration for GPU cluster provisioning

### Out of Scope
- Autoscaling / HPA configuration
- Gateway API routing
- Model caching / PVC for model storage
- Multi-node distributed inference (pipeline parallelism)
- Monitoring / DCGM metrics

## Technical Design

### Models & Hardware

| Manifest | Model | GPU | CPU | Memory | Purpose |
|----------|-------|-----|-----|--------|---------|
| `vllm-qwen.yaml` | Qwen/Qwen2.5-1.5B-Instruct | 1 | 2 | 8Gi | Lightweight test |
| `vllm-embedding.yaml` | BAAI/bge-base-en-v1.5 | 1 | 2 | 4Gi | Embedding |
| `vllm-kimi.yaml` | moonshotai/Kimi-K2.5 | 8 | 16 | 64Gi | Production |

### Crossplane XRD Parameters (User-Facing)

The XRD deliberately exposes a minimal surface. End-users (developers requesting inference) should not need to understand vLLM internals, shared memory sizing, tensor parallelism, or health probe tuning. The composition function derives all of that from the inputs below.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `model` | string | yes | - | HuggingFace model identifier |
| `gpu` | integer | no | 1 | Number of GPUs |
| `ingressHost` | string | no | - | Ingress hostname for external access (e.g., `qwen.127.0.0.1.nip.io`) |
| `providerConfigName` | string | yes | - | Name of the `kubernetes.crossplane.io` ProviderConfig for the target cluster |

### Composition Function Internals (Derived from Inputs)

The Python composition function maps user inputs to `VLLMRuntime` CR fields and Ingress. These are **not exposed** in the XRD — the function computes them:

| Derived Field | Logic |
|---------------|-------|
| `cpu` | Scaled from GPU count (e.g., 1 GPU → 2 CPU, 8 GPU → 16 CPU) |
| `memory` | Scaled from GPU count (e.g., 1 GPU → 8Gi, 8 GPU → 64Gi) |
| `shmSize` | Scaled from GPU count |
| `tensorParallelSize` | Set to GPU count when > 1 |
| `gpuMemoryUtilization` | Fixed at 0.9 |
| `dtype` | Fixed at auto |
| `VLLM_WORKER_MULTIPROC_METHOD` | Set to `spawn` when GPU > 1 |
| `initialDelaySeconds` | Scaled from GPU count (more GPUs → larger model → longer startup) |
| `huggingfaceTokenSecret` | Injected from a convention-based secret name if the model requires it |

### Composed Resources

The Python composition function generates `kubernetes.m.crossplane.io/v1alpha1` Object resources (namespace-scoped). Each Object wraps one of:

1. **`VLLMRuntime` CR** (`production-stack.vllm.ai/v1alpha1`) — model, GPU, compute resources, vLLM args, env vars, probes. All fields derived from `model` + `gpu` inputs.
2. **`Ingress`** (`networking.k8s.io/v1`) — conditional, only when `ingressHost` is provided.

Objects use `providerConfigRef` with `kind: ClusterProviderConfig` to target the user-specified cluster. Manifests inside Objects include explicit `namespace` (from the XR) to satisfy provider-kubernetes requirements.

### Composition Function

Inline Python script executed by `function-python` engine (`xpkg.crossplane.io/crossplane-contrib/function-python`). Source lives in `python/composition.py` and is embedded into the Composition YAML via `task package-generate`. Acts as the translation layer between the minimal user-facing API and the underlying vLLM Production Stack + Ingress resources. Key responsibilities:
- Map `model` + `gpu` to full `VLLMRuntime` spec (compute, parallelism, probes, env vars)
- Apply sensible defaults and scaling heuristics (more GPUs → more memory, longer probe delays, tensor parallelism enabled)
- Conditionally inject HuggingFace token for gated models
- Conditionally generate Ingress when `ingressHost` is set

### Testing Strategy

- **Chainsaw e2e tests**: Verify composed Object resources (and underlying VLLMRuntime + Ingress) against KinD (no GPU nodes, no cost)
- **Manual testing**: Plain manifests on a real GPU cluster

## Dependencies

- NVIDIA GPU Operator on cluster nodes (GPU driver, device plugin)
- Crossplane installed via Helm (for Configuration phase)
- vLLM Production Stack operator installed on the target cluster (for Phase 2)
- provider-kubernetes with RBAC for VLLMRuntime, Ingress
- dot-kubernetes Configuration for provisioning GPU clusters
- HuggingFace token for gated models (Kimi K2.5)

## Success Criteria

- Plain manifests deploy successfully with `kubectl apply` and serve inference requests
- Python unit tests pass for all composition function scenarios
- Crossplane claims create functional VLLMRuntime + Ingress via the Composition
- All three scenarios (lightweight, embedding, production) work through the same XRD API
- Deleting a claim cleans up all created resources
- Configuration published to Upbound Marketplace

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| vLLM image version incompatibility | Manifests fail to start | Pin to lmcache/vllm-openai:v0.3.13 (operator requires lmcache fork) |
| Python function SDK breaking changes | Function stops working | Pin SDK version in requirements.txt |
| provider-kubernetes RBAC missing | Objects fail to create | Document RBAC setup in prerequisites |
| Kimi K2.5 requires model-specific args | Won't work with generic Composition | Composition function handles model-specific args internally |
| vLLM Production Stack CRD is alpha (`v1alpha1`) | Breaking changes between operator versions | Pin operator version; treat upgrades as explicit work items |

## Milestones

### Phase 0: Scaffolding (shared infrastructure for all phases)
- [x] devbox.json with all required packages (kubectl, helm, kind, crossplane CLI, chainsaw, python, etc.)
- [x] Taskfile.yml with tasks for cluster lifecycle (e.g., `cluster-create`, `cluster-destroy`)
- [x] KinD cluster as Crossplane control plane
- [x] Crossplane installed via Helm
- [x] Cloud provider credentials configured (e.g., GCP)
- [x] GPU cluster provisioned via dot-kubernetes Crossplane Configuration

### Phase 1: Plain K8s Manifests
- [x] Create vllm-qwen.yaml, vllm-embedding.yaml, vllm-kimi.yaml (each with VLLMRuntime CR + Ingress)
- [x] Install vLLM Production Stack operator on destination GPU cluster (manual)
- [x] Notify crossplane-kubernetes project to include vLLM Production Stack operator in GPU cluster provisioning
- [x] Test manifests on GPU cluster (Qwen and embedding validated; Kimi requires 8-GPU cluster)

### Phase 2: Crossplane Configuration
- [x] Project scaffolding (Taskfile.yml, .chainsaw.yaml)
- [x] Python composition function
- [x] XRD, Composition, and Configuration manifest
- [x] Provider package declarations
- [ ] vLLM Production Stack operator installed on GPU cluster
- [x] Example XRs for all scenarios
- [x] Chainsaw e2e tests passing against KinD
- [x] Published to Upbound Marketplace

### Phase 3: Scale Discussion
- [ ] Discussion: multi-region, multi-cluster, autoscaling, model caching, KV-cache routing, disaggregated prefill-decode, Gateway API Inference Extension, llm-d integration

## Decision Log

| Date | Decision | Rationale | Impact |
|------|----------|-----------|--------|
| 2026-02-16 | Add Ingress to each manifest using nip.io domains | Models need to be accessible from external agents (e.g., Claude Code); without Ingress they are only reachable inside the cluster | Moved Ingress from Out of Scope to In Scope; each manifest now includes Deployment + Service + Ingress; added `ingressHost` XRD parameter; composition function generates conditional Ingress |
| 2026-02-16 | Use vLLM Production Stack operator (`VLLMRuntime` CR) as underlying resource in Phase 2 instead of raw Deployments | Lighter than KServe (no Istio/Knative), tightest integration with vLLM, CRD fields map naturally to our needs. KServe's ops features (canary, autoscaling) are redundant when Crossplane owns orchestration | Phase 2 composition generates `VLLMRuntime` + `Ingress`; added operator as dependency; added alpha CRD risk |
| 2026-02-16 | Minimal XRD surface — only `model`, `gpu`, `ingressHost` exposed to users | The Crossplane Configuration is a company-specific abstraction. End-users requesting inference should not need to understand vLLM internals. Everything else (CPU, memory, shm, tensor parallelism, probes, env vars) is derived by the composition function from the inputs | Reduced XRD from 11 fields to 3; composition function owns all heuristics and defaults; cleaner end-user experience |
| 2026-02-16 | Add Phase 0 for shared scaffolding with Taskfile automation | Testing raw manifests on a GPU cluster requires Crossplane infrastructure (KinD + provider creds + dot-kubernetes). This is shared setup for all phases, not Phase 2 work. Taskfile automates repeatable operations so anyone can `task cluster-create` instead of following manual steps | Added Phase 0 with devbox.json, Taskfile.yml, KinD, Crossplane, provider creds, GPU cluster provisioning; moved devbox.json and Taskfile.yml out of Phase 2 |
| 2026-02-17 | Use `lmcache/vllm-openai` image instead of official `vllm/vllm-openai` | The vLLM Production Stack operator hardcodes `/opt/venv/bin/vllm` as the entrypoint, which only exists in the lmcache fork. The official image places the binary at `/usr/local/bin/vllm` and is incompatible | All manifests use `lmcache/vllm-openai:v0.3.13`; composition function must use this image too |
| 2026-02-17 | Reduce Qwen CPU from 4 to 2 | GKE `n1-standard-4` GPU nodes have ~3.9 allocatable CPUs after system pods; requesting 4 CPUs caused scheduling failures | Updated Qwen manifest and Models & Hardware table; composition function CPU heuristic adjusted (1 GPU → 2 CPU) |
| 2026-02-17 | Move manifests from `k8s/` to `examples/` | All deployment examples (cluster claims, Crossplane config, vLLM manifests, docs) belong together; manifests are examples, not part of the Crossplane Configuration package built in Phase 2 | Consolidated all files under `examples/`; Ingress service port corrected from 8000 to 80 (operator creates service on port 80) |
| 2026-02-18 | Use `function-python` engine instead of custom function OCI image | Follow same pattern as crossplane-kubernetes with function-kcl: reference generic function engine, embed logic inline. No Dockerfile or separate function package needed | Eliminated function build/push workflow; Python code lives in `python/composition.py`, embedded into Composition YAML via `task package-generate` |
| 2026-02-18 | Add `providerConfigName` as required XRD field | Composed resources (Object CRs) need to target a specific cluster via provider-kubernetes ProviderConfig. User must specify which cluster to deploy vLLM resources to | XRD has 4 fields (model, gpu, ingressHost, providerConfigName); examples use `gpu-cluster`; tests use `incluster` |
| 2026-02-18 | Crossplane v2 — namespace-scoped XRs, no Claims | Crossplane v2 removes Claims; XRs are namespace-scoped directly via `scope: Namespaced` in XRD | Examples are `llm-*.yaml` (not `claim-*.yaml`); XRD uses `apiextensions.crossplane.io/v2` |
| 2026-02-18 | Use namespace-scoped Objects (`kubernetes.m.crossplane.io/v1alpha1`) with `ClusterProviderConfig` | Namespace-scoped XRs (Crossplane v2) require namespace-scoped composed resources. Cluster-scoped Objects (`kubernetes.crossplane.io/v1alpha2`) cannot be owned by namespace-scoped XRs. `ClusterProviderConfig` (cluster-scoped) ensures accessibility from any namespace (e.g., random chainsaw test namespaces) | Composition generates `kubernetes.m.crossplane.io/v1alpha1` Objects; `providerConfigRef` includes `kind: ClusterProviderConfig`; manifests include explicit `namespace`; ProviderConfig changed to `ClusterProviderConfig` |
| 2026-02-18 | Chainsaw Foreground deletion propagation | Without Foreground propagation, namespace deletion races Crossplane resource cleanup — Objects with finalizers block namespace teardown, causing stuck Terminating namespaces | `.chainsaw.yaml` uses `deletion.propagation: Foreground`; delete timeout set to 5m for Crossplane cascade; removed redundant cleanup scripts |

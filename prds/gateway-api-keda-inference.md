# PRD: Inference-Aware Gateway Routing and KEDA Autoscaling

**Status**: Deferred
**Priority**: High
**Created**: 2026-02-22
**Replaces**: PRD #3 (Autoscaling with KEDA) and PRD #4 (Gateway API Inference Extension), both now closed

## Problem Statement

The current `LLMInference` composition treats all requests equally (round-robin via basic Ingress) and runs a fixed number of replicas regardless of demand. This wastes GPU resources when idle and delivers poor latency under load. LLM inference needs two capabilities that work together:

1. **Inference-aware routing** — Route requests based on model name, loaded LoRA adapters, KV-cache state, and pod saturation rather than round-robin
2. **Event-driven autoscaling** — Scale replicas based on LLM-specific metrics (queue depth, KV-cache pressure) rather than CPU/memory, including scale-to-zero for cost savings

These are combined into a single PRD because they share infrastructure (Envoy Gateway), interact directly (scale-to-zero requires the gateway's request-holding capability), and represent a single user-facing concern: "how my inference workload handles traffic."

## Context

### Lessons from crossplane-app

The [crossplane-app Gateway + KEDA PRD] implements the same Gateway API + KEDA pattern for non-inference workloads first. Key patterns to adopt from that work:

- **Composition patterns**: How to conditionally generate HTTPRoute vs Ingress, how to compose ScaledObject resources
- **XRD API design**: The routing and scaling API structure (field names, defaults, backward compatibility approach)
- **Testing approach**: KEDA + Envoy Gateway in KinD without cloud dependencies
- **ScaledObject targeting**: How KEDA targets operator-managed Deployments

What's **new** in this PRD (not covered by crossplane-app):
- Gateway API Inference Extension resources (InferencePool, InferenceObjective)
- vLLM-specific KEDA triggers (queue depth, KV-cache pressure)
- Priority-based traffic management and load shedding
- Scale-to-zero with request holding (Inference Extension capability)

### Infrastructure Prerequisites

dot-kubernetes will install the following as system-level components:
- **Envoy Gateway** — Gateway API implementation
- **KEDA** — Event-driven autoscaler
- **Gateway API Inference Extension** — Inference-aware routing add-on (InferencePool CRD + Body-Based Router)
- **A default Gateway resource** — HTTP/HTTPS listeners that routes attach to

## Proposed API Surface

### Routing

Replace the current `ingressHost` field with Gateway API routing (Ingress removed entirely):

```yaml
apiVersion: inference.devopstoolkit.ai/v1alpha1
kind: LLMInference
metadata:
  name: llama-3
  namespace: production
spec:
  model: meta-llama/Llama-3.3-70B-Instruct
  gpu: 8
  providerConfigName: inference-large
  host: llama.example.com
  criticality: critical   # or "sheddable" (default: critical)
```

When `host` is set:
- Generate `InferencePool` (targets vLLM pods by label selector, references EPP)
- Generate `InferenceObjective` (sets priority from criticality, references pool)
- Generate `HTTPRoute` (routes from Gateway to InferencePool)

When `host` is omitted:
- No routing resources generated

### Scaling

Replica fields follow the crossplane-app pattern — `minReplicas`/`maxReplicas` at top level:

```yaml
spec:
  minReplicas: 1       # default: 1 (set 0 for scale-to-zero)
  maxReplicas: 4       # default: 3
  scaling:
    enabled: true
    metric: queue-depth  # or "kv-cache" (default: queue-depth)
```

**Behavior**:
- `minReplicas` sets `VLLMRuntime.deploymentConfig.replicas` directly (static replica count when scaling disabled)
- When `scaling.enabled: true`, generate a KEDA `ScaledObject` targeting the VLLMRuntime CR (via scale subresource) and a `ServiceMonitor` for Prometheus scraping of vLLM metrics
- `metric: queue-depth` → Prometheus trigger on `vllm:num_requests_waiting` with threshold (default: 3 per replica)
- `metric: kv-cache` → Prometheus trigger on `vllm:kv_cache_usage_perc` with threshold (default: 0.8)
- `minReplicas: 0` enables scale-to-zero with KEDA

**What we intentionally don't expose**:
- `cooldownPeriod`, `pollingInterval`, raw Prometheus queries — the composition function picks sensible defaults
- Custom trigger definitions — keep the API simple, optimize for the 90% case
- SLO-based scaling (TTFT/ITL latency targets) — future enhancement after the basics are proven

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
  providerConfigName: inference-large
  host: llama.example.com
  criticality: critical
  minReplicas: 1
  maxReplicas: 4
  scaling:
    enabled: true
    metric: queue-depth
```

This generates:
1. `Object` wrapping `VLLMRuntime` (existing)
2. `Object` wrapping `InferencePool` (new — targets vLLM pods, references EPP service)
3. `Object` wrapping `InferenceObjective` (new — sets priority for pool traffic)
4. `Object` wrapping `HTTPRoute` (new — routes from Gateway to InferencePool)
5. `Object` wrapping EPP `ConfigMap` (new — scoring plugin configuration)
6. `Object` wrapping EPP `ServiceAccount` (new — RBAC identity)
7. `Object` wrapping EPP `Role` pod-reader (new — get/watch/list pods)
8. `Object` wrapping EPP `Role` pool-reader (new — get/watch/list InferencePool CRDs)
9. `Object` wrapping EPP `RoleBinding` pod-reader (new)
10. `Object` wrapping EPP `RoleBinding` pool-reader (new)
11. `Object` wrapping EPP `Deployment` (new — endpoint picker pod)
12. `Object` wrapping EPP `Service` (new — gRPC on port 9002)
13. `Object` wrapping `ServiceMonitor` (new — Prometheus scraping of vLLM metrics, required for KEDA triggers)
14. `Object` wrapping `ScaledObject` (new — KEDA autoscaler targeting VLLMRuntime via scale subresource)

## Implementation Approach

### Phase 1: XRD schema extension ✅

- [x] Added `host`, `criticality`, `minReplicas`, `maxReplicas`, `scaling` fields to XRD
- [x] Removed `ingressHost` field and Ingress generation from composition
- [x] `minReplicas` drives `VLLMRuntime.deploymentConfig.replicas` (was hardcoded `1`)
- [x] Tests and examples updated, all passing

### Phase 2: Gateway API routing resources ✅

- [x] InferencePool generation: selector `app: <name>` matches VLLMRuntime operator pods, targetPort 8000, endpointPickerRef to `<name>-epp:9002`
- [x] InferenceObjective generation: maps `criticality` to `priority` (critical=100, sheddable=0), references pool
- [x] HTTPRoute generation: parentRef to Gateway `eg` in `envoy-gateway-system`, backendRef to InferencePool
- [x] Gateway API CRDs and Inference Extension CRDs installed in KinD test cluster
- [x] E2e tests passing for routing resources (both default namespace and targetNamespace)

### Phase 3: KEDA autoscaling ✅

- [x] ScaledObject generation: Prometheus trigger querying vLLM metrics, target Deployment name matches VLLMRuntime CR name
- [x] `queue-depth` metric: `sum(vllm:num_requests_waiting{namespace="..."})` with threshold 3 (per replica)
- [x] `kv-cache` metric: `avg(vllm:kv_cache_usage_perc{namespace="..."})` with threshold 0.8
- [x] Prometheus address as XRD field `scaling.prometheusAddress` with default `http://kube-prometheus-stack-prometheus.prometheus-system:9090`
- [x] `minReplicaCount`/`maxReplicaCount` from XR fields, `scaleTargetRef` by VLLMRuntime name
- [x] E2e tests passing for ScaledObject generation (queue-depth trigger in target-ns)

### Phase 4: Test infrastructure and test cases

- [x] Install Gateway API Inference Extension CRDs in KinD (v1-manifests.yaml + experimental-manifests.yaml)
- [x] Install Gateway API CRDs in KinD (standard-install.yaml)
- [x] Install KEDA CRDs in KinD (keda-2.19.0-crds.yaml)
- [x] Add test cases for scaling (queue-depth ScaledObject generation with targetNamespace)
- [x] Add test cases for kv-cache metric and scale-to-zero
- [x] Add test case for no-host scenario (no routing resources generated)

### Resolved Questions

- **VLLMRuntime pod labels**: Operator sets `app.kubernetes.io/name: <vllmruntime-name>` on pods (confirmed from live GKE cluster validation; original assumption of `app:` label was incorrect)
- **VLLMRuntime Deployment name**: Same as VLLMRuntime CR name (confirmed from operator source)
- **InferenceModel vs InferenceObjective**: InferenceModel no longer exists in v1.3.1. Replaced by `InferenceObjective` (priority-based traffic management) and `InferenceModelRewrite` (model name routing). We use InferenceObjective.
- **KEDA vs VLLMRuntime replicas conflict**: The VLLMRuntime operator continuously reconciles `Deployment.spec.replicas` from `deploymentConfig.replicas`, fighting KEDA when ScaledObject targets the Deployment. Solution: KEDA must target VLLMRuntime CR directly via `/scale` subresource. This requires patching the VLLMRuntime CRD to add `scale` subresource with `specReplicasPath: .spec.deploymentConfig.replicas` and `statusReplicasPath: .spec.deploymentConfig.replicas`. Feature request sent to crossplane-kubernetes. When scaling is enabled, composition does NOT set `deploymentConfig.replicas` (KEDA owns it via scale subresource); when scaling is disabled, composition sets `deploymentConfig.replicas = minReplicas` directly.
- **ServiceMonitor required for KEDA**: Prometheus does not scrape vLLM metrics without a ServiceMonitor. Composition generates a ServiceMonitor with `release: kube-prometheus-stack` label (required by Prometheus operator's `serviceMonitorSelector`) targeting the vLLM service.
- **EPP args**: EPP v1.3.1 requires `--config-file /config/default-plugins.yaml` (crashes without it). Envoy AI Gateway ext_proc requires TLS, so `--secure-serving=false` must NOT be set (EPP defaults to TLS).
- **KV-cache metric name**: Actual vLLM Prometheus metric is `vllm:kv_cache_usage_perc` (not `gpu_cache_usage_perc`). Confirmed from live vLLM metrics endpoint.
- **Model choice for T4 GPUs**: Qwen2.5-3B works on T4 (compute capability 7.5). Qwen2.5-7B crashes (Flash Attention 2 requires compute capability 8.0+). Qwen2.5-1.5B processes requests too fast to build queue for KEDA testing.
- **Prometheus endpoint**: Exposed as `scaling.prometheusAddress` XRD field with default `http://kube-prometheus-stack-prometheus.prometheus-system:9090` (matches dot-kubernetes setup). Feature request sent to crossplane-app to add same default.
- **dot-kubernetes Cluster API fields**: Confirmed from `crossplane-kubernetes` XRD — `apps.envoyGateway.enabled`, `apps.keda.enabled`, `apps.prometheus.enabled`, `apps.vllm.enabled`, `apps.nvidia.enabled` all available. `gatewayInferenceExtension.enabled` is a separate dot-kubernetes PRD (not yet implemented). Cluster examples updated to use new fields.
- **Envoy AI Gateway**: dot-kubernetes v2.0.18 (crossplane-kubernetes PR #272) installs Envoy Gateway with AI Gateway Helm values (`extensionManager.backendResources` for InferencePool) and Inference Extension CRDs (GA + experimental) when `envoyGateway.enabled: true`. No separate flag needed. Phase 5 guide validation unblocked.
- **Envoy AI Gateway full architecture**: crossplane-kubernetes confirmed full extensionManager config: `hooks.xdsTranslator` (listener/route/cluster/secret translation + post hooks), `service.fqdn` pointing to `ai-gateway-controller.envoy-ai-gateway-system.svc.cluster.local:1063`, `extensionApis.enableBackend: true`, plus AI Gateway controller Helm chart deployed to `envoy-ai-gateway-system`. EPP (Endpoint Picker Pod) is per-InferencePool responsibility of crossplane-inference.
- **EPP always deployed with host**: EPP is architecturally required for InferencePool-based routing regardless of replica count. Envoy Gateway cannot route to InferencePool backends without the EPP ext_proc gRPC service. Overhead is minimal (one lightweight Go pod) compared to GPU workloads.
- **VLLMRuntime CRD scale subresource — DEAD END**: crossplane-kubernetes added `/scale` subresource to the VLLMRuntime CRD, but live GKE validation revealed two fatal issues: (1) `statusReplicasPath` must point to `.status.*` not `.spec.*` — Kubernetes rejects `.spec.deploymentConfig.replicas`; (2) even after fixing the path, the vLLM operator does not populate `.status.replicas` or `.status.selector`, so the HPA cannot discover pods (`"selector is required"` error). The scale subresource is non-functional without operator cooperation. crossplane-kubernetes should remove it.
- **VLLMRuntime operator fights KEDA**: Confirmed by live testing — `kubectl scale deployment qwen --replicas=2` reverts to `1` within 30 seconds. The operator continuously reconciles `Deployment.spec.replicas` from `deploymentConfig.replicas`. There is no way to use KEDA with VLLMRuntime today.
- **Industry autoscaling state (Feb 2026)**: Researched how the industry handles vLLM autoscaling. All production KEDA integrations (vLLM Production Stack Helm chart, KServe+KEDA, KAITO) target plain Deployments, not operator CRDs. The vLLM Production Stack 2026 roadmap ([#855](https://github.com/vllm-project/production-stack/issues/855)) lists "Integrate KEDA-based scaling in CRD" as P0, but it's unimplemented. A CRD autoscaler proposal (PR #238) was closed without merging in Sept 2025.
- **Gateway routing without KEDA has minimal value**: Inference-aware routing (EPP) only benefits multi-replica deployments. With static replicas (no KEDA), it's functionally equivalent to basic Ingress for single-replica setups. The complexity (14 composed resources) is not justified without the scaling half of the story.

### Open Questions

None — all technical questions resolved. PRD is deferred pending upstream vLLM operator support for KEDA autoscaling.

### Phase 5: End-to-end setup guides and manual validation

Each guide covers the full flow: control plane setup, GPU cluster provisioning (with Envoy Gateway + KEDA + Inference Extension), deploying LLMInference XRs, and verifying routing + scaling work. Guides replace the outdated `examples/cluster-setup.md`. Each step must be validated by executing commands against real infrastructure.

**Authoring workflow**: Write one command at a time, execute it against real infrastructure, confirm it works, then write the next command. This ensures every step in the guide is validated and reproducible. The GCP guide is written first and establishes the template structure that AWS and Azure guides follow.

**Guide conventions**:
- Use explicit commands (e.g., `kind create cluster`, `helm upgrade --install`) instead of `task` shortcuts — clearer for users who haven't seen the Taskfile
- Use `yq` for YAML field substitution (e.g., setting `host` to the real Gateway IP) instead of `sed`
- Reuse existing `kind.yaml` for KinD cluster config
- KinD control plane commands use `KUBECONFIG=./kubeconfig.yaml`; GPU cluster commands use the cloud provider's kubeconfig

**Prerequisite**: Confirm dot-kubernetes Cluster API fields for Envoy Gateway, KEDA, Inference Extension, and Prometheus (check `../crossplane-kubernetes` XRD or coordinate with dot-kubernetes maintainers).

#### Google Cloud (GCP/GKE)
- [x] Update `examples/cluster-gke-small.yaml` and `examples/cluster-gke-large.yaml` for new infra (Envoy Gateway, KEDA, Prometheus instead of Traefik)
- [x] Write `examples/google-cloud.md` — full guide: KinD control plane → dot-kubernetes + dot-inference install → GCP credentials → GPU cluster → deploy LLMInference with Gateway routing + KEDA scaling → verify routing → verify autoscaling
- [ ] Validate every step by executing against real GKE cluster — inference endpoint and KEDA trigger activation validated; scale subresource now available (crossplane-kubernetes implemented), re-validate KEDA autoscaling end-to-end

#### AWS (EKS)
- [x] Update `examples/cluster-aws-small.yaml` for new infra (Envoy Gateway, KEDA, Prometheus instead of Traefik)
- [ ] Write `examples/aws.md` — full guide: same flow adapted for AWS (IAM roles, EKS, GPU node groups)
- [ ] Validate every step by executing against real EKS cluster

#### Azure (AKS)
- [x] Create `examples/cluster-azure-small.yaml` (Envoy Gateway, KEDA, Prometheus, NVIDIA, vLLM)
- [ ] Write `examples/azure.md` — full guide: same flow adapted for Azure (service principal, AKS, GPU node pools)
- [ ] Validate every step by executing against real AKS cluster

#### Cleanup
- [ ] Remove outdated `examples/cluster-setup.md` after all per-hyperscaler guides are complete
- [x] Update LLM example YAMLs (`llm-qwen.yaml` updated: model → Qwen2.5-3B, host → placeholder with nip.io pattern)

## Testing

### Test Environment

Extend the existing KinD test cluster setup:
- Install Gateway API Inference Extension CRDs (GA + experimental) ✅
- Install Gateway API CRDs ✅
- Install KEDA CRDs (v2.19.0) ✅
- Keep the existing VLLMRuntime CRD (no operator needed — tests verify resource creation, not runtime behavior)

### Test Cases

- **No host** — VLLMRuntime generated, no routing resources ✅
- **Gateway API routing** — `host` set → InferencePool + InferenceObjective + HTTPRoute generated ✅
- **Scaling: disabled (default)** — no ScaledObject generated ✅
- **Scaling: queue-depth** — ScaledObject with correct Prometheus trigger ✅
- **Scaling: kv-cache** — ScaledObject with KV-cache metric trigger ✅
- **Scale-to-zero** — `minReplicas: 0` with KEDA enabled succeeds ✅
- **Criticality** — InferenceObjective reflects `critical` (priority 100) vs `sheddable` (priority 0) ✅
- **Custom replicas** — `minReplicas` correctly sets `deploymentConfig.replicas` ✅
- **Full stack** — Gateway routing + KEDA scaling + VLLMRuntime all generated correctly ✅
- **EPP deployment** — `host` set → EPP ConfigMap + ServiceAccount + Roles + RoleBindings + Deployment + Service generated ✅
- **EPP absent without host** — No EPP resources when `host` is omitted ✅

## Future Milestones

- [x] **EPP deployment** — Generate Endpoint Picker Pod (Deployment + Service + ServiceAccount + RBAC + ConfigMap) per InferencePool for KV-cache-aware load balancing. Image: `registry.k8s.io/gateway-api-inference-extension/epp:v1.3.1`. Deployed inside `if host:` block alongside InferencePool.
- [ ] **SLO-based autoscaling** — Add `scaling.metric: latency` option that scales on TTFT/ITL latency targets rather than proxy metrics like queue depth. Requires reliable latency metrics from vLLM and more sophisticated KEDA trigger configuration. To be tackled once queue-depth/kv-cache scaling is proven in production.
- [ ] **Node autoscaling** — Discuss and design how GPU node scaling interacts with KEDA pod scaling. KEDA scales pods, but if no GPU nodes are available, pods stay Pending. Needs coordination with cluster autoscaler or Karpenter to provision GPU nodes on demand.
- [x] **dot-kubernetes: Envoy AI Gateway + Inference Extension** — dot-kubernetes v2.0.18 installs Envoy Gateway with AI Gateway Helm values and Inference Extension CRDs when `envoyGateway.enabled: true` (crossplane-kubernetes PR #272)

## Related PRDs

- [PRD #2: Model Caching with PVs](2-model-caching.md) — faster scale-up and viable scale-to-zero (extends this PRD's value but is not a prerequisite)
- [PRD #5: KV-Cache Routing](5-kv-cache.md) — prefix-cache-aware routing is handled by the Inference Extension's endpoint picker (may already be covered by this PRD)
- [PRD #6: Multi-Cluster Inference](6-multi-cluster.md) — multi-cluster gateway routing builds on this foundation

## Dependencies

- **Upstream**: ~~dot-kubernetes installing Envoy Gateway, KEDA, and Gateway API Inference Extension~~ Resolved — dot-kubernetes v2.0.18 includes Envoy AI Gateway config and Inference Extension CRDs
- **Upstream**: crossplane-app Gateway + KEDA PRD (patterns and lessons learned)
- **Upstream**: ~~dot-kubernetes patching VLLMRuntime CRD with `/scale` subresource~~ Dead end — scale subresource requires operator to populate `.status.replicas` and `.status.selector`, which the vLLM operator does not do. crossplane-kubernetes should remove the scale subresource from the VLLMRuntime CRD.
- **BLOCKING**: vLLM Production Stack must implement native KEDA/HPA support in the VLLMRuntime CRD. Tracked on their [2026 Roadmap (#855)](https://github.com/vllm-project/production-stack/issues/855) as P0: "Integrate KEDA-based scaling in CRD". PRD is deferred until this ships.
- **Downstream**: PRD #5 (KV-Cache) may be partially or fully addressed by the Inference Extension's endpoint picker

## Decision Log

| Date | Decision | Rationale | Impact |
|------|----------|-----------|--------|
| 2026-02-22 | Combined PRDs #3 and #4 into single PRD | Gateway and KEDA interact directly (scale-to-zero requires gateway request holding), share infrastructure (Envoy Gateway), and represent a single user concern | Reduces coordination overhead, enables coherent API design |
| 2026-02-22 | crossplane-app validates pattern first | Non-inference workloads are cheaper to test (no GPUs), prove Gateway + KEDA composition patterns in KinD before adding inference complexity | De-risks implementation, establishes reusable patterns |
| 2026-02-27 | Removed Ingress support entirely | Gateway API is the successor to Ingress; dot-kubernetes installs Envoy Gateway on all target clusters; eliminates dual routing path complexity | No `routing` field needed, `host` triggers Gateway API resources only |
| 2026-02-27 | Adopted crossplane-app `minReplicas`/`maxReplicas` pattern | Consistent API across crossplane-app and crossplane-inference; `minReplicas` serves dual purpose as static replica count and KEDA minimum | Resolves KEDA vs VLLMRuntime replicas conflict; replaces hardcoded `replicas: 1` |
| 2026-02-27 | Moved from `scaling.min`/`scaling.max` to top-level replica fields | crossplane-app proved this pattern; `minReplicas` is useful even without KEDA (users can set static replica count) | Simpler `scaling` object with only `enabled` and `metric` |
| 2026-02-27 | InferenceObjective replaces InferenceModel | InferenceModel CRD no longer exists in Gateway API Inference Extension v1.3.1; replaced by InferenceObjective (priority-based) and InferenceModelRewrite (model name routing) | `criticality` maps to integer `priority` (critical=100, sheddable=0); InferencePool backendRef uses GA group `inference.networking.k8s.io` |
| 2026-02-27 | Prometheus address as XRD field with default | Follows crossplane-app pattern; `scaling.prometheusAddress` defaults to `http://kube-prometheus-stack-prometheus.prometheus-system:9090` matching dot-kubernetes setup | Works out of the box for standard clusters; override for custom Prometheus |
| 2026-02-27 | Step-by-step write-execute-validate workflow for guides | Writing the full guide upfront risks untested commands; iterating one command at a time against real infrastructure catches issues immediately | Every command in every guide is guaranteed to work; GCP guide establishes template for AWS/Azure |
| 2026-02-27 | Explicit commands instead of `task` shortcuts in guides | Users following the guide may not have the Taskfile or understand `task cluster-create`; explicit `kind`/`helm`/`kubectl` commands are self-explanatory | Guides are standalone and portable; no dependency on project-specific Taskfile |
| 2026-02-27 | Use `yq` for YAML host substitution | `yq` operates on YAML structure (`.spec.host`) rather than raw text; safer and more readable than `sed` regex | Model deployment commands use `yq` to set host field with real Gateway IP before `kubectl apply` |
| 2026-02-27 | Envoy AI Gateway required for InferencePool routing | Standard Envoy Gateway does not recognize `inference.networking.k8s.io` as a valid backendRef group; Envoy AI Gateway adds `extensionManager.backendResources` config. Discovered during live GKE validation | dot-kubernetes must install Envoy Gateway with AI Gateway Helm values; Phase 5 blocked until resolved |
| 2026-02-27 | Envoy AI Gateway blocker resolved | dot-kubernetes v2.0.18 (crossplane-kubernetes PR #272) bundles AI Gateway config and Inference Extension CRDs under `envoyGateway.enabled: true` | Phase 5 guide validation unblocked; no new XRD fields needed |
| 2026-02-28 | EPP always deployed with host | EPP is architecturally required for InferencePool routing (Envoy cannot resolve pool backends without ext_proc). Overhead is minimal (one Go pod) vs GPU workloads. No separate toggle needed. | 8 new Object resources per LLMInference when `host` is set; composition grows but routing is fully functional |
| 2026-02-28 | ServiceMonitor required for Prometheus vLLM scraping | Prometheus won't scrape vLLM metrics without a ServiceMonitor with `release: kube-prometheus-stack` label. Without metrics, KEDA triggers never fire. | New composed resource (ServiceMonitor) added to composition; always generated regardless of scaling/routing config |
| 2026-02-28 | EPP requires `--config-file` and TLS | EPP v1.3.1 crashes without `--config-file /config/default-plugins.yaml`. Envoy AI Gateway ext_proc cluster uses TLS by default; `--secure-serving=false` causes `WRONG_VERSION_NUMBER` errors. | EPP args updated; removed `--secure-serving=false`, added `--config-file` flag |
| 2026-02-28 | KV-cache metric name correction | Live vLLM Prometheus metrics show `vllm:kv_cache_usage_perc` not `vllm:gpu_cache_usage_perc` | Composition and PRD corrected |
| 2026-02-28 | Qwen2.5-3B for T4 GPU guides | 7B model crashes on T4 (Flash Attention 2 needs compute capability 8.0+, T4 is 7.5); 1.5B model too fast to build request queue for KEDA testing | GCP guide and `llm-qwen.yaml` use 3B model |
| 2026-02-28 | KEDA must target VLLMRuntime via scale subresource | VLLMRuntime operator continuously reconciles Deployment replicas from `deploymentConfig.replicas`, fighting KEDA. Operator has no HPA awareness (CRD-based autoscaling on their roadmap but not implemented). Scale subresource lets KEDA write replicas on the CR; operator propagates to Deployment. | Feature request sent to crossplane-kubernetes to patch CRD; composition skips `deploymentConfig.replicas` when scaling enabled (KEDA owns it); ScaledObject `scaleTargetRef` will target VLLMRuntime kind |
| 2026-02-28 | `hey` for KEDA load testing | `curl` loops insufficient to build request queue pressure; `hey -n 5000 -c 300` with large `max_tokens` reliably triggers KEDA scaling | Added `hey` to `devbox.json`; GCP guide uses `hey` for autoscaling verification |
| 2026-02-28 | KinD cluster renamed to `crossplane-inference` | Distinguish from other KinD clusters during development | `kind.yaml` name and `Taskfile.yml` destroy command updated |
| 2026-02-28 | VLLMRuntime scale subresource is a dead end | Live GKE validation: (1) `statusReplicasPath` must be under `.status` not `.spec`; (2) even after fix, operator doesn't populate `.status.replicas` or `.status.selector` so HPA can't discover pods; (3) operator fights KEDA by reconciling Deployment replicas within 30s | Scale subresource approach abandoned; crossplane-kubernetes should remove it from VLLMRuntime CRD |
| 2026-02-28 | KEDA autoscaling blocked on upstream vLLM operator | All production KEDA+vLLM integrations target plain Deployments, not operator CRDs. vLLM 2026 roadmap lists "Integrate KEDA-based scaling in CRD" as P0 but unimplemented. CRD autoscaler proposal (PR #238) closed without merging Sept 2025. | No viable path to KEDA+VLLMRuntime today |
| 2026-02-28 | Defer entire PRD until vLLM operator supports KEDA | Gateway routing without KEDA has minimal value — inference-aware routing only benefits multi-replica deployments, and without dynamic scaling the 14-resource complexity isn't justified over basic Ingress. Routing and scaling are a single user concern; shipping half doesn't deliver the value proposition. | PRD status → Deferred. No code changes merged. Branch will be deleted. Resume when vLLM operator ships KEDA-in-CRD support. |

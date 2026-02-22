# PRD: Inference-Aware Gateway Routing and KEDA Autoscaling

**Status**: Discussion
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
- Gateway API Inference Extension resources (InferencePool, InferenceModel)
- vLLM-specific KEDA triggers (queue depth, KV-cache pressure)
- Model-aware routing and criticality-based load shedding
- Scale-to-zero with request holding (Inference Extension capability)

### Infrastructure Prerequisites

dot-kubernetes will install the following as system-level components:
- **Envoy Gateway** — Gateway API implementation
- **KEDA** — Event-driven autoscaler
- **Gateway API Inference Extension** — Inference-aware routing add-on
- **A default Gateway resource** — HTTP/HTTPS listeners that routes attach to

## Proposed API Surface

### Routing

Replace the current `ingressHost` field with a more flexible routing configuration:

```yaml
apiVersion: devopstoolkit.live/v1beta1
kind: LLMInference
metadata:
  name: llama-3
  namespace: production
spec:
  model: meta-llama/Llama-3.3-70B-Instruct
  gpu: 8
  providerConfigName: inference-large
  routing: gateway-api   # or "ingress" (default, backward compatible)
  host: llama.example.com
  criticality: critical   # or "sheddable" (default: critical)
```

When `routing: gateway-api`:
- Generate `InferencePool` (targets vLLM pods by label selector)
- Generate `InferenceModel` (maps model name to pool, sets criticality)
- Generate `HTTPRoute` (routes from Gateway to InferencePool)
- Do NOT generate `Ingress`

When `routing: ingress` (default):
- Current behavior preserved — generate `Ingress` as today

### Scaling

```yaml
spec:
  scaling:
    enabled: true
    min: 1           # default: 1 (set 0 for scale-to-zero)
    max: 4           # default: 3
    metric: queue-depth  # or "kv-cache" (default: queue-depth)
```

**Behavior**:
- Generate a KEDA `ScaledObject` targeting the VLLMRuntime-managed Deployment
- `metric: queue-depth` → Prometheus trigger on `vllm:num_requests_waiting` with threshold (default: 3 per replica)
- `metric: kv-cache` → Prometheus trigger on `vllm:gpu_cache_usage_perc` with threshold (default: 0.8)
- `min: 0` enables scale-to-zero. Requires `routing: gateway-api` so the Inference Extension can hold requests during scale-up. If `min: 0` with `routing: ingress`, the composition should warn or error

**What we intentionally don't expose**:
- `cooldownPeriod`, `pollingInterval`, raw Prometheus queries — the composition function picks sensible defaults
- Custom trigger definitions — keep the API simple, optimize for the 90% case
- SLO-based scaling (TTFT/ITL latency targets) — future enhancement after the basics are proven

### Full Example

```yaml
apiVersion: devopstoolkit.live/v1beta1
kind: LLMInference
metadata:
  name: llama-3
  namespace: production
spec:
  model: meta-llama/Llama-3.3-70B-Instruct
  gpu: 8
  providerConfigName: inference-large
  routing: gateway-api
  host: llama.example.com
  criticality: critical
  scaling:
    enabled: true
    min: 1
    max: 4
    metric: queue-depth
```

This generates:
1. `Object` wrapping `VLLMRuntime` (existing)
2. `Object` wrapping `InferencePool` (new — targets vLLM pods)
3. `Object` wrapping `InferenceModel` (new — maps model to pool with criticality)
4. `Object` wrapping `HTTPRoute` (new — routes from Gateway to InferencePool)
5. `Object` wrapping `ScaledObject` (new — KEDA autoscaler targeting vLLM Deployment)

## Implementation Approach

### Phase 1: Adopt patterns from crossplane-app

Once the crossplane-app PRD is implemented, port the proven patterns:
- Conditional routing (Ingress vs Gateway API resource generation)
- ScaledObject composition and Deployment targeting
- Test infrastructure setup (KEDA + Envoy Gateway in KinD)

### Phase 2: Add inference-specific resources

Extend the composition function (`python/composition.py`) to generate:

1. **InferencePool**: Selector must match labels set by the VLLMRuntime operator on pods. Includes endpoint picker configuration for KV-cache-aware load balancing
2. **InferenceModel**: Maps the user's `spec.model` to the InferencePool. Sets `criticality` from the XR spec
3. **ScaledObject**: Prometheus trigger querying vLLM metrics. Must target the correct Deployment name (created by VLLMRuntime operator, need to determine naming convention)

### Open Questions

- **VLLMRuntime pod labels**: What labels does the operator apply? Needed for InferencePool selector. Requires inspecting the operator source or a running instance
- **VLLMRuntime Deployment name**: KEDA ScaledObject needs `scaleTargetRef.name`. What naming convention does the operator use? Is it deterministic from the VLLMRuntime name?
- **KEDA vs VLLMRuntime replicas conflict**: If VLLMRuntime has a `replicas` field and KEDA manages the underlying Deployment's replicas, they'll fight. Resolution: either omit `replicas` from VLLMRuntime when KEDA is enabled, or have KEDA target VLLMRuntime's scale subresource
- **Prometheus endpoint**: KEDA needs to know where Prometheus is. Should this be a field in the XR, a convention (`prometheus.monitoring:9090`), or discovered from the cluster?
- **InferenceModel vs InferenceObjective**: The Inference Extension API is evolving. Verify the current stable CRD names and fields before implementation

## Testing

### Test Environment

Extend the existing KinD test cluster setup:
- Install KEDA, Envoy Gateway, and the Gateway API Inference Extension
- Install Gateway API CRDs and create a default Gateway
- Keep the existing VLLMRuntime CRD (no operator needed — tests verify resource creation, not runtime behavior)

### Test Cases

- **Routing: Ingress (default)** — existing behavior, no regression
- **Routing: Gateway API** — InferencePool + InferenceModel + HTTPRoute generated, Ingress not generated
- **Scaling: disabled (default)** — no ScaledObject generated
- **Scaling: queue-depth** — ScaledObject with correct Prometheus trigger
- **Scaling: kv-cache** — ScaledObject with KV-cache metric trigger
- **Scale-to-zero** — `min: 0` with `routing: gateway-api` succeeds
- **Scale-to-zero + Ingress** — `min: 0` with `routing: ingress` errors or warns
- **Criticality** — InferenceModel reflects `critical` vs `sheddable`
- **Full stack** — Gateway routing + KEDA scaling + VLLMRuntime all generated correctly

## Related PRDs

- [PRD #2: Model Caching with PVs](2-model-caching.md) — faster scale-up and viable scale-to-zero (extends this PRD's value but is not a prerequisite)
- [PRD #5: KV-Cache Routing](5-kv-cache.md) — prefix-cache-aware routing is handled by the Inference Extension's endpoint picker (may already be covered by this PRD)
- [PRD #6: Multi-Cluster Inference](6-multi-cluster.md) — multi-cluster gateway routing builds on this foundation

## Dependencies

- **Upstream**: dot-kubernetes installing Envoy Gateway, KEDA, and Gateway API Inference Extension
- **Upstream**: crossplane-app Gateway + KEDA PRD (patterns and lessons learned)
- **Downstream**: PRD #5 (KV-Cache) may be partially or fully addressed by the Inference Extension's endpoint picker

## Decision Log

| Date | Decision | Rationale | Impact |
|------|----------|-----------|--------|
| 2026-02-22 | Combined PRDs #3 and #4 into single PRD | Gateway and KEDA interact directly (scale-to-zero requires gateway request holding), share infrastructure (Envoy Gateway), and represent a single user concern | Reduces coordination overhead, enables coherent API design |
| 2026-02-22 | crossplane-app validates pattern first | Non-inference workloads are cheaper to test (no GPUs), prove Gateway + KEDA composition patterns in KinD before adding inference complexity | De-risks implementation, establishes reusable patterns |

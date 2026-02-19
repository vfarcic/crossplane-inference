# PRD #3: Autoscaling with KEDA

**Status**: Discussion
**Priority**: High
**Created**: 2026-02-19
**GitHub Issue**: [#3](https://github.com/vfarcic/crossplane-inference/issues/3)

## Problem Statement

GPU inference workloads have highly variable demand. Without autoscaling, users either over-provision GPUs (wasting $8,000-$13,000/month per H100) or under-provision and accept request queuing during traffic spikes. Traditional CPU/memory-based HPA is wrong for LLM workloads — GPU utilization at 100% means efficiency, not overload.

## Context

KEDA is the recommended autoscaling approach for vLLM Production Stack (integrated in Helm chart v0.1.9+). The right scaling signal for LLM inference is `vllm:num_requests_waiting` (queue depth), not GPU or CPU utilization. KEDA supports scale-to-zero, Prometheus triggers without Prometheus Adapter, and configurable cooldown periods.

The vLLM Production Stack exposes metrics via a `/metrics` Prometheus endpoint: queue depth, active requests, GPU cache usage, TTFT/ITL histograms.

## Discussion Topics

### Scaling Signal
- **Queue depth** (`vllm:num_requests_waiting`): Simple, effective. But what threshold? 1 waiting request? 5? Model-dependent?
- **SLO-driven**: Scale based on TTFT or inter-token latency percentiles. Maps directly to user experience but requires defining SLO targets.
- **GPU cache pressure** (`vllm:gpu_cache_usage_perc`): Indicates KV-cache memory pressure. Could trigger scaling before queuing starts.
- **Composite signals**: Combine multiple metrics. More robust but harder to tune.

### API Surface
- Minimal: `replicas: {min: 1, max: 3}` — let the composition function pick KEDA defaults.
- Medium: Add `scalingMetric: queue-depth | slo-latency` to choose the strategy.
- Full: Expose `cooldownPeriod`, `pollingInterval`, threshold values. Probably too much for the target audience.
- Should autoscaling be a separate XR or part of `LLMInference`?

### Infrastructure Prerequisites
- KEDA must be installed on the target cluster. Should dot-kubernetes install it? Should dot-inference check for it?
- Prometheus must be scraping vLLM pods. The vLLM Production Stack operator handles ServiceMonitor creation, but Prometheus itself needs to exist.
- What if the user doesn't have Prometheus/KEDA? Graceful degradation (just don't autoscale) or error?

### Scale-to-Zero
- KEDA supports scaling to zero replicas. Saves GPU cost when idle.
- But cold start (model loading) takes minutes. Only viable with model caching (PRD #2).
- The Gateway API Inference Extension supports flow control for scale-from-zero (holds requests while pods start). Interaction with PRD #4.

### VLLMRuntime vs Deployment Scaling
- KEDA targets Deployments. VLLMRuntime creates a Deployment. Does the ScaledObject target the VLLMRuntime-created Deployment?
- Or does it target VLLMRuntime's `replicas` field? Need to understand the operator's reconciliation behavior.
- If VLLMRuntime replicas are set by the user AND by KEDA, they'll fight.

## Open Questions

- Does vLLM Production Stack operator support KEDA natively (via VLLMRuntime spec) or do we need a separate ScaledObject?
- What's the minimum viable autoscaling config for a user who just wants "handle traffic spikes"?
- How does autoscaling interact with multi-GPU tensor parallelism? Each replica needs N GPUs — scaling from 1 to 2 replicas on an 8-GPU model requires 16 GPUs.

## Related PRDs

- [PRD #2: Model Caching](2-model-caching.md) — prerequisite for fast scale-up and scale-to-zero
- [PRD #4: Gateway API Inference Extension](4-gateway-api-inference.md) — flow control for scale-from-zero
- [PRD #5: KV-Cache Routing](5-kv-cache.md) — multiple replicas enable KV-cache sharing

## Decision Log

| Date | Decision | Rationale | Impact |
|------|----------|-----------|--------|

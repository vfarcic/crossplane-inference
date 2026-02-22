# PRD #6: Multi-Cluster Inference Patterns

**Status**: Discussion
**Priority**: Medium
**Created**: 2026-02-19
**GitHub Issue**: [#6](https://github.com/vfarcic/crossplane-inference/issues/6)

## Problem Statement

Production inference workloads need to span multiple GPU clusters for resilience (failover when a region is down), latency (serve users from the nearest cluster), cost optimization (GPU pricing varies up to 60% between regions), and capacity (no single cluster has enough GPUs for peak demand).

Crossplane's multi-cluster model already supports deploying the same model to multiple clusters via separate `LLMInference` XRs targeting different `providerConfigName` values. The gap is **request routing** — directing user traffic to the right cluster based on availability, latency, or capacity.

## Context

Today, deploying to multiple clusters requires creating separate `LLMInference` XRs:

```yaml
# Cluster 1 (us-east)
apiVersion: inference.devopstoolkit.ai/v1alpha1
kind: LLMInference
metadata:
  name: qwen-east
spec:
  model: Qwen/Qwen2.5-1.5B-Instruct
  providerConfigName: inference-us-east

# Cluster 2 (eu-west)
apiVersion: inference.devopstoolkit.ai/v1alpha1
kind: LLMInference
metadata:
  name: qwen-europe
spec:
  model: Qwen/Qwen2.5-1.5B-Instruct
  providerConfigName: inference-eu-west
```

Each XR independently deploys and reports readiness. There's no coordination between them for routing, failover, or capacity balancing.

## Discussion Topics

### Routing Strategies
- **DNS-based (GeoDNS)**: Route by geographic proximity. Simple, widely supported. No awareness of queue depth or GPU availability.
- **Global load balancer**: Cloud-provider global LB (GCP Global LB, AWS Global Accelerator, Cloudflare). Health-check-aware but not inference-aware.
- **Multi-cluster Gateway API**: Emerging support in GKE Gateway and Istio for cross-cluster routing. Could combine with Inference Extension for inference-aware multi-cluster routing.
- **Application-level routing**: A lightweight proxy that queries each cluster's vLLM metrics and routes to the least-loaded. Most intelligent but another component to manage.

### API Design
- **Option A: No change** — users create multiple XRs manually. Routing is external. Document the pattern.
- **Option B: Multi-cluster XR** — a new `LLMInferenceFleet` XR that takes a list of `providerConfigName` values and deploys to all of them. Routing config included.
- **Option C: Cluster selector** — instead of explicit providerConfigName, use labels to select clusters (e.g., `clusterSelector: {gpu: "a100", region: "us-*"}`). Crossplane control plane resolves which clusters match.

### Capacity-Aware Placement
- GORGO (Feb 2026 paper) showed 2.5x TTFT reduction by jointly optimizing prefix cache locality + network latency + queue state across regions.
- Should dot-inference be aware of cluster capacity, or is that a higher-level orchestration concern?
- Crossplane knows the state of all clusters (via XR status). Could a composition function make placement decisions?

### Failover
- What happens when a GPU cluster goes down? Manual intervention, or automatic rerouting?
- Crossplane will show the XR as not-ready, but the routing layer needs to react.
- Health checks at the routing layer (global LB) vs Crossplane-level readiness propagation.

### Cost Optimization
- GPU pricing varies by region and cloud provider. A100 on GCP us-central1 vs europe-west4 can differ 20-40%.
- Spot/preemptible GPU instances add another dimension — cheaper but can be reclaimed.
- Should dot-inference consider cost in placement decisions, or is that out of scope?

## Open Questions

- Is multi-cluster inference a common enough use case to warrant API-level support, or is documentation sufficient?
- Should routing be part of dot-inference or a separate concern (dot-routing, dot-gateway)?
- How does multi-cluster interact with KV-cache? Cache is local to a cluster — cross-cluster cache sharing is not practical today.
- What's the minimum viable multi-cluster story? (Probably: same model on 2 clusters + DNS failover.)

## Related PRDs

- [PRD #9: Gateway Routing and KEDA Autoscaling](gateway-api-keda-inference.md) — per-cluster autoscaling and gateway routing
- [PRD #7: Disaggregated Inference with llm-d](7-llm-d.md) — llm-d currently targets single-cluster; multi-cluster disaggregation is future work

## Decision Log

| Date | Decision | Rationale | Impact |
|------|----------|-----------|--------|

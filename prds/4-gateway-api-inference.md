# PRD #4: Gateway API Inference Extension

**Status**: Discussion
**Priority**: High
**Created**: 2026-02-19
**GitHub Issue**: [#4](https://github.com/vfarcic/crossplane-inference/issues/4)

## Problem Statement

The current `LLMInference` composition generates a basic `Ingress` for external access. This treats all inference requests equally — round-robin routing to any available pod. LLM inference requests are long-running, partially stateful (LoRA adapters loaded on specific pods, KV-cache is local), and vary dramatically in resource cost. Naive routing leads to suboptimal GPU utilization and unpredictable latency.

## Context

The Gateway API Inference Extension (Kubernetes SIG, `InferencePool` v1 GA/stable) adds inference-aware routing to standard gateways. An Endpoint Selection Extension examines live pod metrics (queue depth, GPU memory, loaded LoRA adapters, prefix cache state) and picks the optimal backend per-request. Supported by kgateway (conformant), Istio, Envoy Gateway, and GKE Gateway. vLLM is the default model server with zero additional configuration.

### Key CRDs
- **InferencePool**: Defines a pool of model-serving pods with routing extension config
- **InferenceObjective**: Maps user-facing model names to backends, defines request criticality (critical vs sheddable)
- **HTTPRoute**: Routes to InferencePool as backend (replaces Ingress)

## Discussion Topics

### Replacing vs Supplementing Ingress
- Should Gateway API routing **replace** Ingress entirely, or should users choose between them?
- If optional, what's the XRD API? A `routing: ingress | gateway-api` field? Or auto-detect based on cluster capabilities?
- Ingress is universally available. Gateway API Inference Extension requires a compatible gateway implementation. Fallback strategy?

### What to Generate
- Current: `Ingress` (1 resource)
- Proposed: `InferencePool` + `InferenceObjective` + `HTTPRoute` (3 resources)
- The `InferencePool` selector needs to match pods created by VLLMRuntime. What labels does the operator set?
- Should we also generate a `Gateway` resource, or assume one exists?

### LoRA Adapter Support
- The Inference Extension supports LoRA affinity — routing requests to pods that already have the required adapter loaded.
- This implies the `LLMInference` API might need a way to specify LoRA adapters, which is currently out of scope.
- Or is LoRA routing purely an infrastructure concern handled at the gateway level?

### Request Criticality
- InferenceObjective supports `Critical` (must serve) vs `Sheddable` (can drop under load).
- Should this be exposed in the XRD? It maps to production vs development/batch workloads.
- Could default to `Critical` and let advanced users override.

### Scale-to-Zero Integration
- The Inference Extension supports flow control for scale-from-zero — holds incoming requests while pods start.
- Direct interaction with KEDA autoscaling (PRD #3). If both are configured, they need to coordinate.

### Gateway Implementation Choice
- kgateway, Istio, Envoy Gateway, GKE Gateway — which to target?
- Should dot-kubernetes pre-install one? Or is the gateway the user's choice?
- Multi-gateway support or pick one and document alternatives?

## Open Questions

- What labels does the VLLMRuntime operator apply to pods? (Needed for InferencePool selector.)
- Should the Gateway API Inference Extension be a new XR (`LLMInferenceGateway`) or integrated into `LLMInference`?
- What's the migration path for existing users on basic Ingress?
- Does this interact with dot-kubernetes's Traefik installation? Traefik doesn't support the Inference Extension today.

## Related PRDs

- [PRD #3: Autoscaling with KEDA](3-autoscaling.md) — scale-to-zero flow control
- [PRD #5: KV-Cache Routing](5-kv-cache.md) — prefix-cache-aware routing is a gateway feature
- [PRD #6: Multi-Cluster Inference](6-multi-cluster.md) — multi-cluster gateways for cross-cluster routing

## Decision Log

| Date | Decision | Rationale | Impact |
|------|----------|-----------|--------|

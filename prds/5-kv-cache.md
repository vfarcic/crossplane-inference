# PRD #5: KV-Cache Routing and Sharing

**Status**: Discussion
**Priority**: Medium
**Created**: 2026-02-19
**GitHub Issue**: [#5](https://github.com/vfarcic/crossplane-inference/issues/5)

## Problem Statement

When running multiple replicas of the same model (enabled by autoscaling, PRD #3), each replica maintains its own KV-cache in GPU memory. Requests with shared prefixes (system prompts, document context, multi-turn conversations) recompute KV tensors redundantly across replicas. Without cache-aware routing, cache hit rates are low and GPU compute is wasted on repeated prefill work.

## Context

**KV-cache** stores the key/value tensors computed during the attention mechanism for each token in the context. It's often the dominant consumer of GPU memory and avoids reprocessing the entire prompt for each new token.

**LMCache** enables sharing KV-cache across vLLM instances via a remote cache server. Claims up to 15x throughput improvement for workloads with shared prefixes, 87% cache hit rates, and 88% faster TTFT for warm hits.

The VLLMRuntime CRD has an `lmcacheConfig` field for configuring LMCache. The Gateway API Inference Extension (PRD #4) supports prefix-cache-aware scoring in its endpoint selection.

## Discussion Topics

### Cache Architecture
- **Per-pod GPU cache only**: Current default. No sharing. Simplest but wastes compute on repeated prefixes.
- **LMCache server**: Separate deployment that stores KV-cache blocks. Multiple vLLM instances read/write to the same server. Adds a dependency but enables cross-replica sharing.
- **CPU offload**: Spill KV-cache from GPU to CPU memory on the same node. Extends effective cache size without sharing.
- **Hierarchical**: GPU memory → CPU memory → LMCache server. Most efficient but most complex.

### Routing Integration
- Prefix-aware routing (directing requests to replicas that already hold relevant cache) is a **gateway-level** concern.
- If Gateway API Inference Extension (PRD #4) is deployed, it handles this automatically via prefix cache scoring.
- Without the Inference Extension, the vLLM Production Stack's built-in router provides basic prefix-aware routing.
- Should dot-inference configure routing, or leave it to the gateway?

### API Surface
- Should the `LLMInference` XRD expose KV-cache config at all? Or is this purely an operational concern?
- Minimal: `kvCache: {enabled: true}` — deploy LMCache server alongside VLLMRuntime.
- Medium: Add `cacheSize`, `offloadToCPU`, `storageBackend` options.
- Or: leave VLLMRuntime's `lmcacheConfig` field alone and let operators configure it directly on the target cluster.

### When Is This Valuable?
- Only matters with **multiple replicas** of the same model (requires autoscaling, PRD #3).
- Highest value for workloads with shared prefixes: multi-turn chat, RAG with shared document context, batch processing with common system prompts.
- Marginal value for diverse, unrelated requests.

### Prefill-Decode Disaggregation Overlap
- KV-cache transfer is the mechanism that enables P/D disaggregation (PRD #7).
- LMCache is the transfer layer used by both llm-d and vLLM Production Stack for P/D.
- Should KV-cache sharing and P/D disaggregation be addressed together or separately?

## Open Questions

- Does the vLLM Production Stack operator deploy LMCache automatically when `lmcacheConfig` is set in VLLMRuntime?
- What are the storage/memory requirements for an LMCache server? How does it scale with model size and request volume?
- Is LMCache a hard dependency or does vLLM gracefully degrade without it?
- How does KV-cache eviction work across replicas? Can stale cache cause correctness issues?

## Related PRDs

- [PRD #9: Gateway Routing and KEDA Autoscaling](gateway-api-keda-inference.md) — multiple replicas (KEDA) and prefix-cache-aware routing (Inference Extension)
- [PRD #7: Disaggregated Inference with llm-d](7-llm-d.md) — KV-cache transfer for P/D disaggregation

## Decision Log

| Date | Decision | Rationale | Impact |
|------|----------|-----------|--------|

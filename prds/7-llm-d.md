# PRD #7: Disaggregated Inference with llm-d

**Status**: Discussion
**Priority**: Low
**Created**: 2026-02-19
**GitHub Issue**: [#7](https://github.com/vfarcic/crossplane-inference/issues/7)

## Problem Statement

LLM inference has two fundamentally different phases — **prefill** (processes input prompt in parallel, compute-bound) and **decode** (generates tokens one at a time, memory-bandwidth-bound). When both run on the same GPU pool, they interfere: prefill bursts starve decode workers (unpredictable inter-token latency), and decode requests block prefill (increased time-to-first-token). At scale with large models (120B+ parameters, long contexts), this interference becomes the dominant performance bottleneck.

## Context

**llm-d** (Red Hat, launched May 2025, v0.4 Dec 2025) is a Kubernetes-native distributed inference framework built on vLLM. It provides disaggregated prefill-decode serving, KV-cache-aware routing via the Gateway API Inference Extension, expert parallelism for MoE models, and a variant autoscaler. The DistServe paper showed 7.4x more requests served with P/D disaggregation.

Key components:
- **vLLM** as the inference engine (same engine dot-inference already uses)
- **Inference Gateway** built on Envoy + Gateway API Inference Extension
- **NIXL** (NVIDIA interconnect library) for GPU-to-GPU KV-cache transfer via RDMA
- **ModelService** Helm chart as the primary deployment interface

Current maturity: early production. Primarily Red Hat OpenShift AI users. Strong industry backing (Google Cloud, IBM, NVIDIA, CoreWeave, AMD).

## Discussion Topics

### When Does This Matter?
- P/D disaggregation shows clear wins for **large models** (120B+), **long inputs** (10k+ tokens), and **MoE architectures** (DeepSeek, Mixtral).
- For small models (1.5B-7B) on 1-2 GPUs, the overhead of disaggregation exceeds the benefit.
- Is the target user base for dot-inference running models large enough to benefit?

### Integration Approach
- **Option A: Crossplane wraps llm-d Helm charts** — Use `provider-helm` to deploy `llm-d-infra` (prerequisites) and `ModelService` charts as composed resources. The XR abstracts the Helm values.
- **Option B: Manage ModelService CRs directly** — If llm-d's ModelService becomes a proper CRD (currently Helm-chart-based), use `provider-kubernetes` to create ModelService resources, similar to current VLLMRuntime pattern.
- **Option C: Separate XR** — New `LLMInferenceDistributed` XR for llm-d workloads, keeping `LLMInference` simple for single-node deployments.
- **Option D: Wait** — llm-d is evolving rapidly. Track maturity and revisit when the API stabilizes.

### Relationship to vLLM Production Stack
- vLLM Production Stack and llm-d are complementary. Production Stack is the simpler entry point; llm-d is for datacenter-scale.
- Both use vLLM as the engine. The difference is the orchestration layer above it.
- Could dot-inference support both backends? A `backend: vllm-stack | llm-d` field?

### Infrastructure Requirements
- llm-d requires pre-installed infrastructure (`llm-d-infra` with Gateway API, GAIE CRDs).
- RDMA networking (InfiniBand or RoCE) is recommended for KV-cache transfer. TCP works but with higher latency.
- These are cluster-level concerns typically outside Crossplane's scope. Should dot-kubernetes handle llm-d infrastructure?

### Expert Parallelism (MoE)
- llm-d supports wide expert parallelism for Mixture-of-Experts models (DeepSeek V3, Mixtral).
- Different from tensor parallelism (which dot-inference already handles). Expert parallelism distributes experts across GPUs.
- Relevant only for MoE architectures. Worth supporting or too niche?

## Open Questions

- Is ModelService moving toward a proper CRD, or will it remain Helm-chart-based?
- What's the minimum cluster size where P/D disaggregation outperforms colocated serving?
- How does llm-d interact with the VLLMRuntime CRD? Can they coexist on the same cluster?
- Should dot-inference try to abstract the difference between vLLM Production Stack and llm-d, or keep them as separate, explicit choices?

## Related PRDs

- [PRD #3: Autoscaling with KEDA](3-autoscaling.md) — llm-d has its own variant autoscaler
- [PRD #4: Gateway API Inference Extension](4-gateway-api-inference.md) — llm-d uses GAIE for routing
- [PRD #5: KV-Cache Routing](5-kv-cache.md) — KV-cache transfer is the mechanism enabling P/D disaggregation

## Decision Log

| Date | Decision | Rationale | Impact |
|------|----------|-----------|--------|

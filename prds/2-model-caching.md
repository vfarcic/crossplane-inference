# PRD #2: Model Caching with Persistent Volumes

**Status**: Discussion
**Priority**: High
**Created**: 2026-02-19
**GitHub Issue**: [#2](https://github.com/vfarcic/crossplane-inference/issues/2)

## Problem Statement

LLM model weights range from 3 GB (Qwen2.5-1.5B) to 200+ GB (large MoE models). Without caching, every pod restart or scale-up event triggers a full re-download from HuggingFace Hub, adding minutes to startup time. This makes autoscaling impractical and turns routine operations (node maintenance, rolling updates, crash recovery) into multi-minute outages.

## Context

The VLLMRuntime CRD already supports PVC-based model storage natively via `pvcStorage` and `pvcMatchLabels` fields. The vLLM engine can load models from any mounted path. The question is how to expose this through the `LLMInference` API and what caching strategy best fits Crossplane's multi-cluster model.

## Discussion Topics

### Storage Strategy
- **PVC per model**: Each model gets its own PersistentVolume. Simple, but doesn't scale if many models share a cluster.
- **Shared filesystem (NFS/EFS/ReadWriteMany)**: Central model repository accessible by all pods. One download serves all replicas. More complex to provision.
- **Host path caching with node affinity**: Models cached on the node filesystem. Fast but ties pods to specific nodes and breaks multi-node scaling.
- **Init container pre-population**: Pull from S3/GCS/model registry before vLLM starts. Decouples model storage from cluster storage.
- **OCI artifacts**: Package model weights as container images. Leverages existing container runtime caching. Emerging pattern.

### API Surface
- How much should the `LLMInference` XRD expose? Minimal option: a single `modelCache: true` boolean. Maximal option: storage class, size, pre-population source.
- Should the composition function auto-size the PVC based on the model? HuggingFace model cards include size metadata.
- Should caching be opt-in or default behavior?

### Multi-Cluster Considerations
- Different GPU clusters may have different storage backends (GCE PD, EBS, local NVMe).
- Model cache should survive cluster upgrades but not necessarily cluster deletion.
- Who provisions the PV — dot-inference, dot-kubernetes, or the user?

### Interaction with Autoscaling
- PVC with `ReadWriteOnce` blocks multi-replica scaling. Need `ReadWriteMany` or a different approach.
- Startup time directly affects autoscaling responsiveness — caching is a prerequisite for effective KEDA scaling.

## Open Questions

- What's the expected model size distribution for target users? (Determines whether PVC sizing heuristics are feasible.)
- Should dot-kubernetes pre-provision shared model storage on GPU clusters?
- Is there value in a cluster-wide model cache operator, or is per-workload PVC sufficient?

## Related PRDs

- [PRD #9: Gateway Routing and KEDA Autoscaling](gateway-api-keda-inference.md) — caching enables fast scale-up and viable scale-to-zero
- [PRD #5: KV-Cache Routing and Sharing](5-kv-cache.md) — different cache (runtime state vs model weights) but similar storage patterns

## Decision Log

| Date | Decision | Rationale | Impact |
|------|----------|-----------|--------|

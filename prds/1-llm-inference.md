# PRD #1: LLM Inference on Kubernetes with vLLM and Crossplane

**Status**: Draft
**Priority**: High
**Created**: 2026-02-16
**GitHub Issue**: [#1](https://github.com/vfarcic/crossplane-inference/issues/1)

## Problem Statement

Running LLM inference on Kubernetes requires significant boilerplate: GPU resource management, shared memory volumes for tensor parallelism, model-specific CLI arguments, health probes tuned to model load times, and HuggingFace token handling for gated models. Each new model deployment repeats this work with slight variations.

There is no simple, unified API to provision inference workloads across different model sizes (1.5B to 1T parameters), types (generative and embedding), and hardware requirements (1 GPU to 8 GPUs with tensor parallelism).

## Solution Overview

**Phase 1:** Plain Kubernetes YAML manifests for three vLLM scenarios, validated directly with `kubectl apply`.

**Phase 2:** A Crossplane Configuration (`dot-inference`) with a custom Python composition function, published to the Upbound Marketplace. Users specify model name, GPU count, and optional tuning parameters via a single `LLMInference` custom resource at `inference.devopstoolkit.ai/v1alpha1`.

All scenarios use **vLLM** as the inference engine.

## User Journey

### Without this feature:
1. Research vLLM container images, CLI args, and health endpoints
2. Manually write Deployment YAML with GPU resources, /dev/shm volumes, probes
3. Debug tensor parallelism configuration for multi-GPU models
4. Repeat for each model/environment

### With this feature:
1. Apply a plain manifest OR create a Crossplane claim with model name and GPU count
2. Inference endpoint is ready

**Crossplane claim example:**
```yaml
apiVersion: inference.devopstoolkit.ai/v1alpha1
kind: LLMInference
metadata:
  name: my-llm
spec:
  parameters:
    model: "Qwen/Qwen2.5-1.5B-Instruct"
    gpu: 1
```

## Scope

### In Scope
- Plain K8s manifests for 3 vLLM deployments (Qwen2.5-1.5B, bge-base-en-v1.5, Kimi K2.5)
- Custom Python composition function (built as OCI image)
- Crossplane XRD defining `LLMInference` API
- Crossplane Composition referencing the Python function
- Configuration package manifest for Upbound Marketplace
- Chainsaw e2e tests against KinD (no cloud provider costs)
- Python unit tests for the composition function
- Example claims for each scenario
- KinD cluster as Crossplane control plane for testing
- Documentation of dot-kubernetes integration for GPU cluster provisioning

### Out of Scope
- Autoscaling / HPA configuration
- Ingress / Gateway API routing
- Model caching / PVC for model storage
- Multi-node distributed inference (pipeline parallelism)
- Monitoring / DCGM metrics

## Technical Design

### Models & Hardware

| Manifest | Model | GPU | CPU | Memory | Purpose |
|----------|-------|-----|-----|--------|---------|
| `vllm-qwen.yaml` | Qwen/Qwen2.5-1.5B-Instruct | 1 | 4 | 8Gi | Lightweight test |
| `vllm-embedding.yaml` | BAAI/bge-base-en-v1.5 | 1 | 2 | 4Gi | Embedding |
| `vllm-kimi.yaml` | moonshotai/Kimi-K2.5 | 8 | 16 | 64Gi | Production |

### Crossplane XRD Parameters

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `model` | string | yes | - | Model identifier |
| `gpu` | integer | no | 1 | Number of nvidia.com/gpu |
| `cpu` | string | no | "4" | CPU request/limit |
| `memory` | string | no | "8Gi" | Memory request/limit |
| `shmSize` | string | no | "2Gi" | /dev/shm size |
| `tensorParallelSize` | integer | no | 1 | Tensor parallel size |
| `gpuMemoryUtilization` | string | no | "0.9" | GPU memory fraction |
| `dtype` | string | no | "auto" | Data type |
| `extraArgs` | array[string] | no | [] | Additional vLLM CLI args |
| `huggingfaceTokenSecret` | string | no | - | Secret name with HF_TOKEN |

### Composition Function

Custom Python function using `function-sdk-python`, built as OCI image. Handles:
- Dynamic vLLM arg construction
- Conditional HF_TOKEN env var
- Conditional VLLM_WORKER_MULTIPROC_METHOD for multi-GPU
- Scaling initialDelaySeconds based on GPU count

### Testing Strategy

- **Python unit tests**: Test function logic without a cluster
- **Chainsaw e2e tests**: Verify composed Object resources against KinD (no GPU nodes, no cost)
- **Manual testing**: Plain manifests on a real GPU cluster

## Dependencies

- NVIDIA GPU Operator on cluster nodes (GPU driver, device plugin)
- Crossplane installed via Helm (for Configuration phase)
- provider-kubernetes with RBAC for Deployments and Services
- dot-kubernetes Configuration for provisioning GPU clusters
- HuggingFace token for gated models (Kimi K2.5)

## Success Criteria

- Plain manifests deploy successfully with `kubectl apply` and serve inference requests
- Python unit tests pass for all composition function scenarios
- Crossplane claims create functional Deployment + Service via the Composition
- All three scenarios (lightweight, embedding, production) work through the same XRD API
- Deleting a claim cleans up all created resources
- Configuration published to Upbound Marketplace

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| vLLM image version incompatibility | Manifests fail to start | Pin to specific version (v0.13.0) |
| Python function SDK breaking changes | Function stops working | Pin SDK version in requirements.txt |
| provider-kubernetes RBAC missing | Objects fail to create | Document RBAC setup in prerequisites |
| Kimi K2.5 requires model-specific args | Won't work with generic Composition | Use `extraArgs` field for model-specific flags |

## Milestones

### Phase 1: Plain K8s Manifests
- [ ] Create and validate vllm-qwen.yaml, vllm-embedding.yaml, vllm-kimi.yaml
- [ ] Test manifests on a GPU cluster (manual verification)

### Phase 2: Crossplane Configuration
- [ ] Project scaffolding (devbox.json, Taskfile.yml, .chainsaw.yaml)
- [ ] Python composition function with unit tests
- [ ] XRD, Composition, and Configuration manifest
- [ ] Provider package declarations
- [ ] Example claims for all scenarios
- [ ] Chainsaw e2e tests passing against KinD
- [ ] Published to Upbound Marketplace

### Phase 3: Scale Discussion
- [ ] Discussion: multi-region, multi-cluster, autoscaling, model caching, KV-cache routing, disaggregated prefill-decode, Gateway API Inference Extension, llm-d integration

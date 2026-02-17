# Kimi K2.5 (Production Multi-GPU)

Large production model requiring 8 GPUs with tensor parallelism.

## Model Details

| Field | Value |
|-------|-------|
| Model | [moonshotai/Kimi-K2.5](https://huggingface.co/moonshotai/Kimi-K2.5) |
| Type | Chat / Instruct |
| Parameters | Large (gated model) |
| GPU | 8 |
| CPU | 16 |
| Memory | 64Gi |
| Tensor Parallelism | 8 |

## Prerequisites

- Kubernetes cluster with at least 8 NVIDIA GPUs (use `cluster-gke-large.yaml`)
- vLLM Production Stack operator installed (`VLLMRuntime` CRD available)
- Traefik ingress controller (or any ingress controller)
- HuggingFace token for gated model access (see below)

## HuggingFace Token Setup

Kimi K2.5 is a gated model — you must accept the license on HuggingFace and create a token secret:

```bash
kubectl create secret generic huggingface-token \
  --from-literal=token=hf_YOUR_TOKEN_HERE
```

## Deploy

Replace `127.0.0.1` with the Traefik LoadBalancer IP for real clusters:

```bash
INGRESS_IP=$(kubectl get svc -A -l app.kubernetes.io/name=traefik \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')

sed "s/127.0.0.1/${INGRESS_IP}/" examples/vllm-kimi.yaml | kubectl apply -f -
```

Startup will be significantly longer than smaller models due to model size and multi-GPU initialization.

## Test

```bash
# List models
curl http://kimi.${INGRESS_IP}.nip.io/v1/models

# Chat completion
curl http://kimi.${INGRESS_IP}.nip.io/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "moonshotai/Kimi-K2.5",
    "messages": [{"role": "user", "content": "Explain quantum computing briefly."}],
    "max_tokens": 200
  }'
```

## Notes

- Uses `lmcache/vllm-openai` image (required by the vLLM Production Stack operator)
- Tensor parallelism splits the model across 8 GPUs — `VLLM_WORKER_MULTIPROC_METHOD=spawn` is required for multi-GPU
- Requires `cluster-gke-large.yaml` (large GPU nodes) — will not fit on the small cluster
- GPU memory utilization set to 0.9 (90%)
- Not yet tested on hardware (requires 8-GPU cluster)

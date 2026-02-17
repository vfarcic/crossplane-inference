# Qwen 2.5 1.5B Instruct

Lightweight generative model for testing and low-resource environments.

## Model Details

| Field | Value |
|-------|-------|
| Model | [Qwen/Qwen2.5-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct) |
| Type | Chat / Instruct |
| Parameters | 1.5B |
| GPU | 1 |
| CPU | 2 |
| Memory | 8Gi |

## Prerequisites

- Kubernetes cluster with at least one NVIDIA GPU node
- vLLM Production Stack operator installed (`VLLMRuntime` CRD available)
- Traefik ingress controller (or any ingress controller)

## Deploy

Replace `127.0.0.1` with the Traefik LoadBalancer IP for real clusters:

```bash
INGRESS_IP=$(kubectl get svc -A -l app.kubernetes.io/name=traefik \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')

sed "s/127.0.0.1/${INGRESS_IP}/" examples/vllm-qwen.yaml | kubectl apply -f -
```

Startup takes 3-5 minutes (image pull ~6 min on first deploy, model load ~1 min, FlashInfer warmup ~2 min on T4).

## Test

```bash
# List models
curl http://qwen.${INGRESS_IP}.nip.io/v1/models

# Chat completion
curl http://qwen.${INGRESS_IP}.nip.io/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen2.5-1.5B-Instruct",
    "messages": [{"role": "user", "content": "What is Kubernetes?"}],
    "max_tokens": 100
  }'
```

## Notes

- Uses `lmcache/vllm-openai` image (required by the vLLM Production Stack operator, which hardcodes `/opt/venv/bin/vllm` as the entrypoint)
- GPU memory utilization set to 0.9 (90%)
- On GKE `n1-standard-4` GPU nodes, CPU is limited to ~3.9 allocatable — the manifest requests 2 CPUs to fit comfortably

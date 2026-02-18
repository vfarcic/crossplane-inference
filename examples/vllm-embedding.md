# BGE Base EN v1.5 (Embedding)

Embedding model for vector search and retrieval-augmented generation (RAG).

## Model Details

| Field | Value |
|-------|-------|
| Model | [BAAI/bge-base-en-v1.5](https://huggingface.co/BAAI/bge-base-en-v1.5) |
| Type | Embedding |
| Parameters | 110M |
| GPU | 1 |
| CPU | 2 |
| Memory | 4Gi |

## Prerequisites

- Kubernetes cluster with at least one NVIDIA GPU node
- vLLM Production Stack operator installed (`VLLMRuntime` CRD available)
- Traefik ingress controller (or any ingress controller)

## Deploy

Replace `127.0.0.1` with the Traefik LoadBalancer IP for real clusters:

```bash
INGRESS_IP=$(kubectl get svc -A -l app.kubernetes.io/name=traefik \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')

sed "s/127.0.0.1/${INGRESS_IP}/" examples/vllm-embedding.yaml | kubectl apply -f -
```

## Test

```bash
# List models
curl http://embedding.${INGRESS_IP}.nip.io/v1/models

# Generate embeddings
curl http://embedding.${INGRESS_IP}.nip.io/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "BAAI/bge-base-en-v1.5",
    "input": "Kubernetes is an open-source container orchestration platform"
  }'
```

## Notes

- Uses `lmcache/vllm-openai` image (required by the vLLM Production Stack operator)
- This is a small model (110M params) — loads quickly and uses minimal GPU memory
- Returns 768-dimensional embedding vectors
- GPU memory utilization set to 0.9 (90%)

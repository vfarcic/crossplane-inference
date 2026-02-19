# dot-inference

A Crossplane Configuration that provides a minimal API for deploying LLM inference workloads on Kubernetes. Specify `model`, `gpu`, `ingressHost`, and `providerConfigName` — the composition function derives everything else (CPU, memory, tensor parallelism, probes, env vars).

```yaml
apiVersion: inference.devopstoolkit.ai/v1alpha1
kind: LLMInference
metadata:
  name: my-llm
spec:
  model: Qwen/Qwen2.5-1.5B-Instruct
  gpu: 1
  ingressHost: qwen.example.com
  providerConfigName: inference-small
```

## Quickstart

### Control Plane Cluster

Create a KinD cluster and install Crossplane:

```bash
kind create cluster --name crossplane-inference --kubeconfig ./kubeconfig.yaml

export KUBECONFIG=$PWD/kubeconfig.yaml

helm repo add crossplane-stable https://charts.crossplane.io/stable

helm repo update

helm upgrade --install crossplane crossplane-stable/crossplane \
    --namespace crossplane-system --create-namespace --wait
```

### Providers & Configurations

Install providers, `dot-kubernetes`, and `dot-inference`:

```bash
kubectl apply --filename providers/provider-kubernetes-incluster.yaml

kubectl apply --filename providers/provider-helm-incluster.yaml

kubectl wait providers --all \
    --for=condition=Healthy --timeout=300s

kubectl apply --filename providers/dot-kubernetes.yaml

kubectl apply --filename config.yaml

sleep 30

kubectl wait configuration dot-kubernetes dot-inference \
    --for=condition=Healthy --timeout=300s

kubectl wait providers --all \
    --for=condition=Healthy --timeout=300s
```

#### GCP Credentials

The instructions below use [dot-kubernetes](https://github.com/vfarcic/crossplane-kubernetes) to provision a GKE cluster with GPU nodes, Traefik ingress, and the vLLM Production Stack operator. For AWS (EKS) or Azure (AKS), see the [dot-kubernetes docs](https://github.com/vfarcic/crossplane-kubernetes).

> **Note:** The GPU cluster needs NVIDIA drivers and the [vLLM Production Stack operator](https://github.com/vllm-project/production-stack). GKE installs NVIDIA drivers automatically on GPU node pools, and `dot-kubernetes` installs the vLLM operator when `vllm.enabled: true`. If you bring your own cluster, install the [NVIDIA GPU Operator](https://github.com/NVIDIA/gpu-operator) and the vLLM Production Stack operator manually.

```bash
export GCP_PROJECT_ID=dot-$(date +%Y%m%d%H%M%S)

gcloud projects create $GCP_PROJECT_ID \
    --name="Crossplane Inference"

gcloud billing accounts list

export GCP_BILLING_ACCOUNT=[...] # Copy the account ID from the list above

gcloud billing projects link $GCP_PROJECT_ID \
    --billing-account=$GCP_BILLING_ACCOUNT

gcloud services enable container.googleapis.com \
    --project=$GCP_PROJECT_ID

gcloud iam service-accounts create crossplane \
    --project=$GCP_PROJECT_ID

gcloud projects add-iam-policy-binding $GCP_PROJECT_ID \
    --member="serviceAccount:crossplane@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/container.admin" --condition=None --format=none

gcloud projects add-iam-policy-binding $GCP_PROJECT_ID \
    --member="serviceAccount:crossplane@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/compute.admin" --condition=None --format=none

gcloud projects add-iam-policy-binding $GCP_PROJECT_ID \
    --member="serviceAccount:crossplane@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/iam.serviceAccountUser" --condition=None --format=none

gcloud iam service-accounts keys create ./gcp-creds.json \
    --iam-account=crossplane@${GCP_PROJECT_ID}.iam.gserviceaccount.com \
    --project=$GCP_PROJECT_ID

kubectl create secret generic gcp-creds \
    --namespace crossplane-system \
    --from-file=creds=./gcp-creds.json

envsubst < examples/provider-config-gcp.yaml | kubectl apply --filename -
```

### GPU Cluster

```bash
kubectl create namespace inference

kubectl apply --filename examples/cluster-gke-small.yaml \
    --namespace inference

kubectl wait cluster.devopstoolkit.ai inference-small \
    --namespace inference \
    --for=condition=Ready --timeout=1200s
```

> Use `examples/cluster-gke-large.yaml` instead for Kimi K2.5 or other models requiring 8 GPUs.

### Deploy Inference

`dot-kubernetes` automatically creates a `ProviderConfig` named after the cluster (`inference-small`).

Get the GPU cluster kubeconfig and Traefik external IP, then update the ingress host in the example:

```bash
KUBECONFIG=gpu-kubeconfig.yaml gcloud container clusters \
    get-credentials inference-small \
    --region us-east1 --project $GCP_PROJECT_ID

export INGRESS_IP=""
while [ -z "$INGRESS_IP" ]; do
    INGRESS_IP=$(KUBECONFIG=gpu-kubeconfig.yaml \
        kubectl get svc -n traefik \
        -l app.kubernetes.io/name=traefik \
        -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}' 2>/dev/null)
    [ -z "$INGRESS_IP" ] && sleep 10
done
echo "Traefik IP: $INGRESS_IP"

cat examples/llm-qwen.yaml \
    | sed "s/127.0.0.1/$INGRESS_IP/" \
    | kubectl apply --namespace inference --filename -

kubectl wait llminference qwen --namespace inference \
    --for=condition=Ready --timeout=900s
```

Wait for the model to load (this can take 5+ minutes as the model downloads and vLLM starts):

```bash
while ! curl -s http://qwen.$INGRESS_IP.nip.io/v1/models | grep -q Qwen; do
    echo "Model is not yet ready, waiting..."
    sleep 30
done
echo 'Model is ready!'
```

Validate the inference endpoint:

```bash
curl -s http://qwen.$INGRESS_IP.nip.io/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "Qwen/Qwen2.5-1.5B-Instruct",
      "messages": [{"role": "user", "content": "Say hello"}]
    }'
```

> See `examples/llm-embedding.yaml` for an embedding model, or `examples/llm-kimi.yaml` for Kimi K2.5 (requires `cluster-gke-large.yaml`).

### Cleanup

Delete the inference workload:

```bash
kubectl delete llminference qwen --namespace inference

kubectl wait llminference qwen --namespace inference \
    --for=delete --timeout=300s
```

Delete the GPU cluster:

```bash
kubectl delete cluster.devopstoolkit.ai inference-small \
    --namespace inference

kubectl wait cluster.devopstoolkit.ai inference-small \
    --namespace inference --for=delete --timeout=1200s

while kubectl --namespace inference get managed 2>/dev/null | grep -v '^NAME' \
    | grep -q .; do
    echo "Managed resources still being deleted..."
    sleep 30
done
echo 'All managed resources deleted.'
```

Delete the GCP project (removes all cloud resources):

```bash
gcloud projects delete $GCP_PROJECT_ID

rm -f ./gcp-creds.json ./gpu-kubeconfig.yaml
```

Delete the control plane cluster:

```bash
kind delete cluster --name crossplane-inference

rm -f ./kubeconfig.yaml
```

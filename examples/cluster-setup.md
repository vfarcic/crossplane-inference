# GPU Cluster Setup

How to provision a GPU-enabled GKE cluster using Crossplane and the `dot-kubernetes` Configuration, then deploy vLLM inference models to it.

## Architecture

```
KinD (local)                         GKE (cloud)
┌─────────────────────┐              ┌──────────────────────┐
│ Crossplane          │  provisions  │ GPU nodes (T4/A100)  │
│ dot-kubernetes      │ ──────────>  │ Traefik ingress      │
│ GCP providers       │              │ vLLM operator        │
│ provider-kubernetes  │              │ VLLMRuntime CRs      │
│ provider-helm       │              └──────────────────────┘
└─────────────────────┘
```

A local KinD cluster acts as the Crossplane control plane. It provisions a GKE cluster with GPU nodes, Traefik, and the vLLM Production Stack operator.

## Prerequisites

- [devbox](https://www.jetpack.io/devbox/) (provides kubectl, helm, kind, etc.)
- GCP account with billing enabled
- `gcloud` CLI authenticated

## Step 1: Create KinD Control Plane

```bash
task cluster-create
```

This creates a KinD cluster and installs Crossplane.

## Step 2: Install dot-kubernetes Configuration

```bash
KUBECONFIG=./kubeconfig.yaml kubectl apply -f examples/crossplane-config.yaml
KUBECONFIG=./kubeconfig.yaml kubectl wait configuration dot-kubernetes \
  --for=condition=Healthy --timeout=300s
```

This installs the `dot-kubernetes` Configuration from the Upbound Marketplace, which pulls in GCP, AWS, and Azure providers automatically.

## Step 3: Install provider-kubernetes and provider-helm

These must be installed separately (Crossplane Configurations cannot bundle `DeploymentRuntimeConfig` for in-cluster RBAC):

```bash
KUBECONFIG=./kubeconfig.yaml kubectl apply -f examples/provider-kubernetes-incluster.yaml
KUBECONFIG=./kubeconfig.yaml kubectl apply -f examples/provider-helm-incluster.yaml
KUBECONFIG=./kubeconfig.yaml kubectl wait providers \
  crossplane-provider-kubernetes crossplane-provider-helm \
  --for=condition=Healthy --timeout=120s
```

## Step 4: Configure GCP Credentials

Create a GCP project, enable Kubernetes Engine API, and create a service account:

```bash
gcloud projects create crossplane-inference --name="Crossplane Inference"
gcloud billing projects link crossplane-inference --billing-account=YOUR_BILLING_ACCOUNT
gcloud services enable container.googleapis.com --project=crossplane-inference
gcloud iam service-accounts create crossplane --project=crossplane-inference
gcloud projects add-iam-policy-binding crossplane-inference \
  --member="serviceAccount:crossplane@crossplane-inference.iam.gserviceaccount.com" \
  --role="roles/container.admin" --condition=None --format=none
gcloud projects add-iam-policy-binding crossplane-inference \
  --member="serviceAccount:crossplane@crossplane-inference.iam.gserviceaccount.com" \
  --role="roles/compute.admin" --condition=None --format=none
gcloud projects add-iam-policy-binding crossplane-inference \
  --member="serviceAccount:crossplane@crossplane-inference.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser" --condition=None --format=none
gcloud iam service-accounts keys create ./gcp-creds.json \
  --iam-account=crossplane@crossplane-inference.iam.gserviceaccount.com \
  --project=crossplane-inference
```

Create the Kubernetes secret and provider configs:

```bash
KUBECONFIG=./kubeconfig.yaml kubectl create secret generic gcp-creds \
  -n crossplane-system --from-file=creds=./gcp-creds.json

GCP_PROJECT_ID=crossplane-inference envsubst < examples/provider-config-gcp.yaml \
  | KUBECONFIG=./kubeconfig.yaml kubectl apply -f -
```

## Step 5: Provision GPU Cluster

Small cluster (2 T4 GPUs — enough for Qwen and embedding models):

```bash
KUBECONFIG=./kubeconfig.yaml kubectl apply -f examples/cluster-gke-small.yaml
```

Large cluster (needed for Kimi K2.5 with 8 GPUs):

```bash
KUBECONFIG=./kubeconfig.yaml kubectl apply -f examples/cluster-gke-large.yaml
```

Wait for the cluster to be ready (~10-15 minutes):

```bash
KUBECONFIG=./kubeconfig.yaml kubectl wait cluster.devopstoolkit.ai inference-small \
  --for=condition=Ready --timeout=1800s
```

## Step 6: Get GPU Cluster Kubeconfig

```bash
KUBECONFIG=./gpu-kubeconfig.yaml gcloud container clusters get-credentials \
  inference-small --region=us-east1 --project=crossplane-inference
```

## Step 7: Deploy vLLM Models

Get the Traefik external IP and deploy:

```bash
INGRESS_IP=$(KUBECONFIG=./gpu-kubeconfig.yaml kubectl get svc -A \
  -l app.kubernetes.io/name=traefik \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')

# Deploy Qwen (lightweight, 1 GPU)
sed "s/127.0.0.1/${INGRESS_IP}/" examples/vllm-qwen.yaml \
  | KUBECONFIG=./gpu-kubeconfig.yaml kubectl apply -f -

# Deploy embedding model (1 GPU)
sed "s/127.0.0.1/${INGRESS_IP}/" examples/vllm-embedding.yaml \
  | KUBECONFIG=./gpu-kubeconfig.yaml kubectl apply -f -
```

See the individual model docs (`examples/vllm-*.md`) for testing instructions.

## Cleanup

Delete the GPU cluster (stops cloud billing):

```bash
KUBECONFIG=./kubeconfig.yaml kubectl delete -f examples/cluster-gke-small.yaml
```

Delete the KinD control plane:

```bash
task cluster-destroy
```

Delete the GCP project:

```bash
gcloud projects delete crossplane-inference
```

## Cluster Variants

| File | GPU Node Size | Use Case |
|------|---------------|----------|
| `cluster-gke-small.yaml` | Small (T4, 1 GPU/node) | Qwen 1.5B, BGE embedding |
| `cluster-gke-large.yaml` | Large (A100, multi-GPU/node) | Kimi K2.5 (8 GPUs) |

Both clusters include Traefik (ingress) and the vLLM Production Stack operator pre-installed.

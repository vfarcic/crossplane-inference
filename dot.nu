#!/usr/bin/env nu

source scripts/kubernetes.nu
source scripts/common.nu
source scripts/crossplane.nu
source scripts/ingress.nu

def main [] {}

def "main setup" [] {

    rm --force .env

    let provider = "aws"

    main create kubernetes kind

    let provider_data = main apply crossplane --provider $provider

    kubectl apply --filename providers/provider-kubernetes-incluster.yaml

    kubectl apply --filename providers/provider-helm-incluster.yaml

    kubectl wait providers --all --for=condition=Healthy --timeout=300s

    kubectl apply --filename providers/dot-kubernetes.yaml

    kubectl apply --filename config.yaml

    sleep 30sec

    kubectl wait configuration dot-kubernetes dot-inference --for=condition=Healthy --timeout=300s

    kubectl wait providers --all --for=condition=Healthy --timeout=300s

    if $provider == "google" {
        (
            main apply crossplane-providerconfig $provider
                --google-project-id $provider_data.project_id
        )
    } else {
        main apply crossplane-providerconfig $provider
    }

    kubectl create namespace inference

    main print source

}

def "main destroy" [] {

    kubectl delete llminference qwen --namespace inference

    kubectl wait llminference qwen --namespace inference --for=delete --timeout=300s

    main delete ingress traefik --kubeconfig gpu-kubeconfig.yaml

    kubectl delete cluster.devopstoolkit.ai inference-small --namespace inference

    kubectl wait cluster.devopstoolkit.ai inference-small --namespace inference --for=delete --timeout=1200s

    while (try { kubectl --namespace inference get managed | lines | skip 1 | length } catch { 0 }) > 0 {
        print "Managed resources still being deleted..."
        sleep 30sec
    }
    print "All managed resources deleted."

    if env.PROVIDER == "google" {
        gcloud projects delete $env.PROJECT_ID
        rm --force gcp-creds.json gpu-kubeconfig.yaml
    }


    # kind delete cluster --name crossplane-inference

    rm --force kubeconfig-dot.yaml

    main destroy kubernetes kind

}

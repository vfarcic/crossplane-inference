from crossplane.function.proto.v1 import run_function_pb2 as fnv1
from crossplane.function import response


def compose(req: fnv1.RunFunctionRequest, rsp: fnv1.RunFunctionResponse):
    oxr = req.observed.composite.resource

    spec = oxr["spec"]
    model = spec["model"]
    gpu = int(spec["gpu"]) if "gpu" in spec else 1
    ingress_host = spec["ingressHost"] if "ingressHost" in spec else ""
    provider_config_name = spec["providerConfigName"]
    name = oxr["metadata"]["name"]
    namespace = oxr["metadata"]["namespace"]

    # Derive compute resources from GPU count (2 CPU / 8Gi per GPU)
    cpu = str(2 * gpu)
    memory = "{}Gi".format(8 * gpu)

    # Build VLLMRuntime spec
    vllm_spec = {
        "model": {
            "modelURL": model,
            "dtype": "auto",
        },
        "vllmConfig": {
            "gpuMemoryUtilization": "0.9",
        },
        "deploymentConfig": {
            "image": {
                "registry": "docker.io",
                "name": "lmcache/vllm-openai:v0.3.13",
            },
            "replicas": 1,
            "resources": {
                "cpu": cpu,
                "memory": memory,
                "gpu": str(gpu),
            },
        },
    }

    # Multi-GPU: enable tensor parallelism and spawn worker method
    if gpu > 1:
        vllm_spec["vllmConfig"]["tensorParallelSize"] = gpu
        vllm_spec["vllmConfig"]["env"] = [
            {"name": "VLLM_WORKER_MULTIPROC_METHOD", "value": "spawn"},
        ]

    # Object wrapping VLLMRuntime CR
    rsp.desired.resources["vllm-runtime"].resource.update({
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "Object",
        "spec": {
            "forProvider": {
                "manifest": {
                    "apiVersion": "production-stack.vllm.ai/v1alpha1",
                    "kind": "VLLMRuntime",
                    "metadata": {"name": name, "namespace": namespace},
                    "spec": vllm_spec,
                },
            },
            "providerConfigRef": {"name": provider_config_name, "kind": "ClusterProviderConfig"},
        },
    })

    # Conditionally generate Ingress when ingressHost is set
    if ingress_host:
        rsp.desired.resources["ingress"].resource.update({
            "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
            "kind": "Object",
            "spec": {
                "forProvider": {
                    "manifest": {
                        "apiVersion": "networking.k8s.io/v1",
                        "kind": "Ingress",
                        "metadata": {"name": name, "namespace": namespace},
                        "spec": {
                            "rules": [{
                                "host": ingress_host,
                                "http": {
                                    "paths": [{
                                        "path": "/",
                                        "pathType": "Prefix",
                                        "backend": {
                                            "service": {
                                                "name": name,
                                                "port": {"number": 80},
                                            },
                                        },
                                    }],
                                },
                            }],
                        },
                    },
                },
                "providerConfigRef": {"name": provider_config_name, "kind": "ClusterProviderConfig"},
            },
        })

    response.normal(rsp, "Composed VLLMRuntime for {} with {} GPU(s)".format(model, gpu))

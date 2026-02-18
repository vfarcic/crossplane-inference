from crossplane.function.proto.v1 import run_function_pb2 as fnv1


def compose(req: fnv1.RunFunctionRequest, rsp: fnv1.RunFunctionResponse):
    oxr = req.observed.composite.resource

    model = oxr["spec"]["model"]
    gpu = int(oxr["spec"].get("gpu", 1))
    ingress_host = oxr["spec"].get("ingressHost", "")
    provider_config_name = oxr["spec"]["providerConfigName"]
    name = oxr["metadata"]["name"]

    # TODO: Implement composition logic.
    # Derive compute resources from GPU count.
    # Generate VLLMRuntime CR via provider-kubernetes Object.
    # Conditionally generate Ingress via provider-kubernetes Object.
    # Set providerConfigRef.name to provider_config_name on all Objects.
    pass

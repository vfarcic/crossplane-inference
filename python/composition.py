class Composite(BaseComposite):
    def compose(self):
        model = str(self.spec.model)
        gpu = int(self.spec.gpu) if self.spec.gpu else 1
        ingress_host = str(self.spec.ingressHost) if self.spec.ingressHost else ""
        provider_config_name = str(self.spec.providerConfigName)
        name = str(self.metadata.name)
        namespace = str(self.spec.targetNamespace) if self.spec.targetNamespace else str(self.metadata.namespace)

        # Derive compute resources from GPU count (2 CPU / 8Gi per GPU)
        cpu = str(2 * gpu)
        memory = "{}Gi".format(8 * gpu)

        # Object wrapping VLLMRuntime CR
        vllm = self.resources.vllm_runtime('Object', 'kubernetes.m.crossplane.io/v1alpha1')
        vllm.spec.forProvider.manifest.apiVersion = 'production-stack.vllm.ai/v1alpha1'
        vllm.spec.forProvider.manifest.kind = 'VLLMRuntime'
        vllm.spec.forProvider.manifest.metadata.name = name
        vllm.spec.forProvider.manifest.metadata.namespace = namespace
        vllm.spec.forProvider.manifest.spec.model.modelURL = model
        vllm.spec.forProvider.manifest.spec.model.dtype = 'auto'
        vllm.spec.forProvider.manifest.spec.vllmConfig.gpuMemoryUtilization = '0.9'
        vllm.spec.forProvider.manifest.spec.deploymentConfig.image.registry = 'docker.io'
        vllm.spec.forProvider.manifest.spec.deploymentConfig.image.name = 'lmcache/vllm-openai:v0.3.13'
        vllm.spec.forProvider.manifest.spec.deploymentConfig.replicas = 1
        vllm.spec.forProvider.manifest.spec.deploymentConfig.resources.cpu = cpu
        vllm.spec.forProvider.manifest.spec.deploymentConfig.resources.memory = memory
        vllm.spec.forProvider.manifest.spec.deploymentConfig.resources.gpu = str(gpu)
        vllm.spec.providerConfigRef.name = provider_config_name
        vllm.spec.providerConfigRef.kind = 'ProviderConfig'

        # Multi-GPU: enable tensor parallelism and spawn worker method
        if gpu > 1:
            vllm.spec.forProvider.manifest.spec.vllmConfig.tensorParallelSize = gpu
            vllm.spec.forProvider.manifest.spec.vllmConfig.env[0].name = 'VLLM_WORKER_MULTIPROC_METHOD'
            vllm.spec.forProvider.manifest.spec.vllmConfig.env[0].value = 'spawn'

        # Conditionally generate Ingress when ingressHost is set
        if ingress_host:
            ing = self.resources.ingress('Object', 'kubernetes.m.crossplane.io/v1alpha1')
            ing.spec.forProvider.manifest.apiVersion = 'networking.k8s.io/v1'
            ing.spec.forProvider.manifest.kind = 'Ingress'
            ing.spec.forProvider.manifest.metadata.name = name
            ing.spec.forProvider.manifest.metadata.namespace = namespace
            ing.spec.forProvider.manifest.spec.rules[0].host = ingress_host
            ing.spec.forProvider.manifest.spec.rules[0].http.paths[0].path = '/'
            ing.spec.forProvider.manifest.spec.rules[0].http.paths[0].pathType = 'Prefix'
            ing.spec.forProvider.manifest.spec.rules[0].http.paths[0].backend.service.name = name
            ing.spec.forProvider.manifest.spec.rules[0].http.paths[0].backend.service.port.number = 80
            ing.spec.providerConfigRef.name = provider_config_name
            ing.spec.providerConfigRef.kind = 'ProviderConfig'

        self.results.info('Composed', 'Composed VLLMRuntime for {} with {} GPU(s)'.format(model, gpu))

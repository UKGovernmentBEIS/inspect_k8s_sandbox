# agent-env

![Version: 0.12.0](https://img.shields.io/badge/Version-0.12.0-informational?style=flat-square)

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| additionalResources | list | `[]` | A list of additional resources to deploy within the agent environment. They're passed through the Helm template engine. String values are passed through the template engine then converted to YAML. |
| allowCIDR | list | Empty list (no additional CIDR ranges compared to default policies) | A list of CIDR ranges (e.g. 1.1.1.1/32) that pods within the agent environment are allowed to access. |
| allowDomains | list | Empty list (no internet access) | A list of fully qualified domain names that pods within the agent environment are allowed to access. |
| allowEntities | list | Empty list (no additional entities compared to default policies) | A list of Cilium entities (e.g. "world") that pods within the agent environment are allowed to access. |
| annotations | object | `{}` | A dict of annotations to apply to resources within the agent environment. |
| corednsCommand | list | `["/coredns","-conf","/etc/coredns/Corefile"]` | The command to use for the coredns container. |
| corednsImage | string | `"coredns/coredns:1.8.3"` | The image to use for the coredns container. |
| global | object | set by inspect | The name of the agent environment, only overwrite in cases where e.g. name lengths are causing failures. |
| imagePullSecrets | list | `[]` | References to pre-existing secrets that contain registry credentials. |
| labels | object | `{}` | A dict of labels to apply to resources within the agent environment. |
| networks | object | `{}` | Defines network names that can be attached to services in order to specify subsets of services that can communicate with one another. |
| services | object | see [values.yaml](./values.yaml) | A collection of services to deploy within the agent environment. A service can connect to another service using DNS, e.g. `http://nginx:80`. |
| services.default | object | see [values.yaml](./values.yaml) | The default service, this is required for the agent environment to function. |
| services.default.additionalDnsRecords | list | `[]` | A list of additional domains which will resolve to this service from within the agent environment (e.g. example.com). If one or more records are provided, `dnsRecord` is automatically set to true. |
| services.default.args | list | `[]` | The container's entrypoint arguments. |
| services.default.command | list | `["tail","-f","/dev/null"]` | The container's entrypoint command. |
| services.default.dnsRecord | bool | false | Whether to create a DNS record which will resolve to this service from within the agent environment, using the service name as the domain (e.g. default). |
| services.default.env | list | `[]` | Environment variables that will be set in the container. |
| services.default.image | string | `"python:3.12-bookworm"` | The container's image name. |
| services.default.imagePullPolicy | string | `nil` | The container's image pull policy. |
| services.default.livenessProbe | object | `{}` | A probe which is used to determine when to restart a container. |
| services.default.nodeSelector | object | `{}` | Node selector settings for the Pod. |
| services.default.ports | list | `[]` | Deprecated. All ports of services with a DNS record are accessible (though not necessarily open) to other services within the agent environment. If one or more ports are provided, `dnsRecord` is automatically set to true. |
| services.default.readinessProbe | object | `{}` | A probe which is used to determine when the container is ready to accept. traffic. |
| services.default.resources | object | see [templates/services.yaml](./templates/services.yaml) | Resource requests and limits for the container. |
| services.default.runtimeClassName | string | `"gvisor"` | The container runtime e.g. gvisor or runc. The default is gvisor if not specified or set to `null`. |
| services.default.securityContext | object | `{}` | Privilege and access control settings for the container. |
| services.default.tolerations | list | `[]` | Toleration settings for the Pod. |
| services.default.volumeMounts | list | `[]` | Volume mounts that will be mounted in the container. Volumes defined in `volumes:` as colon-separated strings will automatically be mounted at their specified mount paths. |
| services.default.volumes | list | `[]` | Volumes accessible to the container. Supports arbitrary yaml or colon-separated strings of the form `volume-name:/mount-path`. |
| services.default.workingDir | string | `nil` | The container's working directory. |
| volumes | object | `{}` | A dict of volumes to deploy within the agent environment as NFS-CSI PersistentVolumeClaims. These volumes can be mounted in services using the `volumes:` field. The actual volume name will include the release name. |


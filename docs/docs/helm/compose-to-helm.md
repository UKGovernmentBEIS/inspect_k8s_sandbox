# Automatic Docker Compose to Helm Values Translation

The `k8s_sandbox` package supports automatically converting Docker Compose files to Helm
values files which are compatible with the [built-in Helm chart](built-in-chart.md) at
run time. This is done transparently: files won't be added to your repository.

```py
return Task(
    ...,
    sandbox=("k8s", "compose.yaml"),
)
```

The following file names are supported for automatic translation: `compose.yaml`,
`compose.yml`, `docker-compose.yaml`, `docker-compose.yml`. You must explicitly specify
the relevant compose file name in the `sandbox` parameter; only `helm-values.yaml` and
`values.yaml` are automatically discovered. This is to prevent unintentional translation
of Docker Compose files (e.g. if your Helm values file were misnamed).

Docker Compose files are first validated against the [Compose
Spec](https://github.com/compose-spec/compose-spec).

## Rationale

This functionality intends to facilitate running some of the community-maintained evals
which have not been (and may never be) ported to Helm `values.yaml`. Whilst it is easy
to convert a `compose.yaml` file to a `values.yaml` file, it does add a maintenance
burden, especially if an individual making changes in future does not have access to a
Kubernetes cluster to test the changes.

## Limitations

Only basic Docker compose functionality is supported. For more complex needs, please
write a Helm values file directly.

Images will have to be available to the Kubernetes cluster; they won't be built or
pushed for you.

For internal, non-community eval suites, native Helm `values.yaml` files are still
preferred over the automatic translation of `compose.yaml` files for a number of
reasons:

- To support the whole set of Helm chart and Kubernetes features
- To explicitly _not_ support Docker for certain evals (reducing maintenance burden and
  discourage use of Docker which lacks security features of Kubernetes)
- To be more expressive about which services should get a DNS entry
- To support more powerful readiness and liveness probes

## Default Service

The default service resolution follows the same rules as [Inspect sandboxing doc](https://inspect.aisi.org.uk/sandboxing.html#multiple-environments):

> If you define multiple sandbox environments the default sandbox environment will be
> determined as follows:
>
> 1. First, take any sandbox environment named `default`;
> 2. Then, take any environment with the `x-default` key set to `true`;
> 3. Finally, use the first sandbox environment as the default.

During conversion, services matching rules 2 or 3 are renamed to `default` to ensure
consistent default service resolution regardless of Kubernetes pod ordering. For rule 2,
the service with `x-default: true` is renamed. For rule 3, the "first" service (determined
by YAML order, not alphabetical order) is renamed. Single-service compose files are left
unchanged.

## Internet Access

As per the built-in Helm chart, internet access is disabled by default. This is in
contrast to Docker Compose. There is no native way of specifying which domains should be
accessible in Docker Compose. To express which domains should be accessible when running
an eval in k8s, use the `x-inspect_k8s_sandbox` extension in the Docker Compose file.

```yaml
services:
  myservice:
    image: ubuntu
x-inspect_k8s_sandbox:
  allow_domains:
    - google.com
```

or

```yaml
services:
  myservice:
    image: ubuntu
x-inspect_k8s_sandbox:
  allow_entities:
    - world
```

## Service Accounts

Use the top-level `x-inspect_k8s_sandbox` extension to select a ServiceAccount and
control Kubernetes API token access. The ServiceAccount must already exist by default,
which allows concurrent sandbox releases to reference an externally managed identity:

```yaml
services:
  myservice:
    image: ubuntu
x-k8s:
  service_account_name: dedicated-sandbox-api-client
  service_account_create: false
  automount_service_account_token: true
  allow_entities:
    - kube-apiserver
```

Only enable `automount_service_account_token` when sandbox code must call the Kubernetes
API. See [Service accounts and Kubernetes API
access](built-in-chart.md#service-accounts-and-kubernetes-api-access) for the security
and RBAC requirements.

## Network Modes

The only supported `network_mode` is `none`, which completely isolates a service from
all network traffic (both ingress and egress). This is useful for evals where the agent
should not have any network access.

```yaml
services:
  isolated-service:
    image: ubuntu
    network_mode: none
```

## Resource Requests and Limits

Use the per-service `x-inspect_k8s_sandbox` extension to set Kubernetes resource
requests and limits that the Docker Compose shortcuts (`mem_limit`, `cpus` and
`deploy.resources`) cannot express. The `resources` block is merged into the
`requests`/`limits` that the converter derives from those shortcuts.

The motivating case is a _request-only_ resource such as `ephemeral-storage` — a
scheduling floor with no eviction cap:

```yaml
services:
  default:
    image: ubuntu
    mem_limit: 32g
    cpus: 4.0
    x-inspect_k8s_sandbox:
      resources:
        requests:
          ephemeral-storage: 80Gi
```

This produces the following Helm values for the service:

```yaml
services:
  default:
    resources:
      limits: {memory: 32Gi, cpu: 4.0}
      requests: {memory: 32Gi, cpu: 4.0, ephemeral-storage: 80Gi}
```

Resource names and values are passed through verbatim, so any Kubernetes resource
(e.g. `hugepages-2Mi`) can be set under either `requests` or `limits`. Setting a
key that a Compose shortcut already populated (e.g. `requests.memory` alongside
`mem_limit`) is rejected rather than silently overridden.

This extension exists because Docker Compose cannot express many Kubernetes
resources. It covers CPU and memory requests/limits (via `cpus`/`mem_limit` and
`deploy.resources`), but has no concept of a disk (`ephemeral-storage`) request or
limit, nor of resources such as `hugepages-*`.

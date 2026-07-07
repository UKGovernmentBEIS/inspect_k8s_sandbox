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

### Swap (`memswap_limit`)

`memswap_limit` is ignored (with an info-level log) because Kubernetes has no
Compose-equivalent per-container swap limit. On typical clusters, swap is disabled on
nodes, so this is a no-op. However, if your cluster enables swap (via the Kubernetes
[NodeSwap](https://kubernetes.io/docs/concepts/cluster-administration/swap-memory-management/)
feature) and your Compose file set `memswap_limit` equal to `mem_limit` to disable swap
for the container, the converted workload may instead be able to use swap. If that
matters, disable swap at the node or pod level.

## Security Options

A `security_opt` **seccomp** entry (`seccomp=<value>` or `seccomp:<value>`) is converted
to a `seccompProfile` in the pod's `securityContext` (merged with any context derived
from `user`). Docker's special values map to Kubernetes profile types:

| Compose value                  | Kubernetes `seccompProfile`                                 |
| ------------------------------ | ----------------------------------------------------------- |
| `seccomp=unconfined`           | `{type: Unconfined}`                                        |
| `seccomp=builtin`              | `{type: RuntimeDefault}`                                    |
| `seccomp=<relative/path.json>` | `{type: Localhost, localhostProfile: <relative/path.json>}` |

Notes:

- `builtin` is Docker's (undocumented) value for its built-in default profile.
  `RuntimeDefault` — the container runtime's default profile — is the closest Kubernetes
  analog, though the runtime's default may differ slightly from Docker's built-in
  profile.
- A profile path must be relative and descending (no absolute paths or `..`); it is
  passed through as `localhostProfile`. **Kubernetes resolves this relative to the
  kubelet's seccomp root, so the profile file must already be pre-staged on every node
  (default `/var/lib/kubelet/seccomp/<path>`).** This is not verified at conversion time;
  a missing profile fails only when the pod is launched. The converter logs an info-level
  reminder.
- Non-seccomp entries (e.g. `apparmor=...`, `no-new-privileges`) have no mapping here and
  are rejected rather than silently dropped, so a workload can't believe a security
  control is applied when it isn't.
- **Runtime caveat (gVisor).** A seccomp profile only changes which syscalls the kernel
  *allows*; it can't add syscalls the runtime doesn't implement. The chart defaults to the
  `gvisor` runtime, whose `personality()` does not support `ADDR_NO_RANDOMIZE`. So a
  profile that permits `personality` in order to run `setarch -R` (the portable way to
  disable ASLR, e.g. for exploitation workloads) has no effect under gVisor — `setarch -R`
  fails with `EINVAL` regardless of the profile. Set `runtime: runc` on the service
  (mapped to `runtimeClassName: runc`) for those workloads.

```yaml
services:
  my-service:
    image: ubuntu
    runtime: runc  # gVisor (the default) can't disable ASLR; see the runtime caveat above
    security_opt:
      - seccomp=profiles/no-aslr.json
```

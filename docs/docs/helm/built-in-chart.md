# Built-in Helm Chart

The `k8s_sandbox` package includes a built-in Helm chart named `agent-env` in
`resources/helm/`.

## About the chart

The built-in Helm chart is designed to take a `values.yaml` (or `helm-values.yaml`) file
which is structured much like a Docker `compose.yaml` file.

This is to simplify the complexities of Kubernetes manifests for users who are not
familiar with them.

!!! tip

    [Automatic translation](compose-to-helm.md) from basic `compose.yaml` files is
    supported.

In addition to the info below, see `agent-env/README.md` and `agent-env/values.yaml` for
a full list of configurable options.

## Container runtime class (gVisor)

The default container runtime class name for every service is `gvisor` which, (depending
on your cluster - see [remote cluster setup page](../getting-started/remote-cluster.md))
should map to the `runsc` runtime handler. You can override this if required.

```yaml
services:
  default:
    image: ubuntu:24.04
    command: ["tail", "-f", "/dev/null"]
    runtimeClassName: runc
```

The above example assumes you have a `RuntimeClass` named `runc` deployed to your
cluster which maps to the `runc` runtime handler.

See the [gVisor page](../security/container-runtime.md) for considerations and
limitations on using gVisor versus `runc`.

??? tip "Use the cluster's default runtime class"

    You can cause the runtime class in the Pod spec to not be set at all by setting
    `runtimeClassName` to the `CLUSTER_DEFAULT` magic string in the relevant services.

    ```yaml
    services:
      default:
        image: ubuntu:24.04
        command: ["tail", "-f", "/dev/null"]
        runtimeClassName: CLUSTER_DEFAULT
    ```

    This has the effect of using your cluster's default runtime class.

    This approach is **not recommended** as it makes your evals cluster-dependent and
    therefore less portable. It is preferable to explicitly state which runtime class
    you require if it is not gVisor. See the [remote cluster
    setup](../getting-started/remote-cluster.md) page for more information on installing
    runtimes and deploying `RuntimeClass` objects which map a name to a runtime handler.

??? tip "View your cluster's runtime classes"

    You can view the runtime classes available in your cluster by running the following
    command:

    ```sh
    kubectl get runtimeclass
    ```

    ```
    NAME     HANDLER   AGE
    gvisor   runsc     42d
    runc     runc      42d
    ```

??? question "Aren't containerd or CRI-O the container runtimes?"

    There are multiple "levels" of container runtime in Kubernetes. containerd or CRI-O
    are the "high level" CRI implementations which Kubernetes uses to manage containers.
    The discussion in this section concerning `runtimeClassName` field on Pod spec is
    about the "lower level" OCI runtimes (like `runc` or `runsc`) which are used to
    actually run the container processes.

## Internet access

By default, containers will not be able to access the internet which is an important
security measure when running untrusted LLM-generated code. If you wish to allow limited
internet access, there are 3 methods, each of which influence the Cilium Network Policy.

1. Populate the `allowDomains` list in your `values.yaml` with one or more Fully
Qualified Domain Names. The following example list allows agents to install packages
from a variety of sources:

    ```yaml
    services:
      default:
        image: ubuntu:24.04
        command: ["tail", "-f", "/dev/null"]
    allowDomains:
      - "pypi.org"
      - "files.pythonhosted.org"
      - "bitbucket.org"
      - "github.com"
      - "raw.githubusercontent.com"
      - "*.debian.org"
      - "*.kali.org"
      - "kali.download"
      - "archive.ubuntu.com"
      - "security.ubuntu.com"
      - "mirror.vinehost.net"
      - "*.rubygems.org"
    ```

    !!! bug

        We're investigating occasional issues with the `allowDomains` feature where
        some network requests which ought to be allowed are blocked. It is yet unclear
        whether this is as a result of our service-based DNS resolution setup or whether
        we're putting Cilium under too much load.

2. Populate the `allowCIDR` list with one or more [CIDR
ranges](https://docs.cilium.io/en/stable/security/policy/language/#cidr-based). These
are translated to `toCIDRs` entries in the Cilium Network Policy:

    ```yaml
    allowCIDR:
      - "8.8.8.8/32"
    ```

3. Populate the `allowEntities` list with one or more
[entities](https://docs.cilium.io/en/stable/security/policy/language/#entities-based).
These get translated to `toEntities` entries in the Cilium Network Policy:

    ```yaml
    allowEntities:
      - "world"
    ```

## DNS

The built-in Helm chart is designed to allow services to communicate with each other
using their service names e.g. `curl nginx`, much like you would in Docker Compose.

To make services discoverable by their service name, set the `dnsRecord` key to `true`.

Additionally, you can specify a list of domains that resolve to a given service e.g.
`curl example.com` could resolve to your `nginx` service.

```yaml
services:
  default:
    image: ubuntu:24.04
    command: ["tail", "-f", "/dev/null"]
  nginx:
    image: nginx:1.27.2
    dnsRecord: true
    additionalDnsRecords:
      - example.com
```

To achieve this, whilst maintaining support for deploying multiple instances of the same
chart in a given namespace, a CoreDNS "sidecar" container is deployed in each service
Pod.

??? question "Why not deploy CoreDNS as its own Pod?"

    Because of the way that Cilium caches DNS responses to determine which IP addresses
    correspond to the FQDN allowlist, CoreDNS service must be co-located on the same
    node which the container making the DNS request are on.

??? question "Why not use `hostAliases` to edit `/etc/hosts`?"

    Instead of using DNS, the `/etc/hosts` file could be modified using
    [HostAliases](https://kubernetes.io/docs/tasks/network/customize-hosts-file-for-pods).
    However, some tools which an agent might try to use (e.g. `nslookup`) do not respect
    `/etc/hosts` and will use DNS instead. Therefore, we chose to use a DNS-based
    approach.

For the containers within your release to use this, rather than the default Kubernetes
DNS service, the `/etc/resolv.conf` of your containers is modified to use `127.0.0.1` as
the nameserver.

Note that the CoreDNS sidecar only binds to `127.0.0.1` so won't be accessible from
outside the Pod.

CoreDNS is used over Dnsmasq for 2 reasons:

* CoreDNS is the default DNS server in Kubernetes.
* It allows you to map domains to known service names whilst delegating resolving the
  actual IP address to the default Kubernetes DNS service.

## Headless services

Any entry under the `services` key in `values.yaml` which sets `dnsRecord: true` or
`additionalDnsRecords` will have a
[headless](https://kubernetes.io/docs/concepts/services-networking/service/#headless-services)
`Service` created for it.

This creates a DNS record in the Kubernetes cluster which resolves to the Pod's IP
addresses directly. This allows agents to use tools like `netcat` or `ping` to explore
their environment. If the service were not headless (i.e. it had a ClusterIP), tools
like `netcat` would only be able to connect to the ports explicitly exposed by the
service.

## Readiness probes

Avoid making assumptions about the order in which your containers will start, or the
time it will take for them to become ready.

Kubernetes knows when a container has started, but it does not know when the container
is ready to accept traffic. In the case of containers such as web servers, there may be
a delay between the container starting and the web server being ready to accept traffic.

Use [readiness
probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/#define-readiness-probes)
as a test to determine if your container is ready to accept traffic. The eval will not
begin until all readiness probes have passed.

```yaml
services:
  nginx:
    image: nginx:1.27.2
    readinessProbe:
      httpGet:
        path: /
        port: 80
      initialDelaySeconds: 5
      periodSeconds: 5
```

You do not need to specify a `readinessProbe` on containers which have a trivial
entrypoint (e.g. `sleep infinity` or `tail -f /dev/null`).

## Resource requests and limits

Default resource limits are assigned for each `service` within the Helm chart. The
limits and requests are equal such that the Pods have a `Guaranteed` [QoS
class](https://kubernetes.io/docs/tasks/configure-pod-container/quality-service-pod/).

```yaml
resources:
  limits:
    memory: "2Gi"
    cpu: "500m"
  requests:
    memory: "2Gi"
    cpu: "500m"
```

These can optionally be overridden for each service.

```yaml
services:
  default:
    image: ubuntu:24.04
    command: ["tail", "-f", "/dev/null"]
    resources:
      limits:
        memory: "1Gi"
        cpu: "250m"
      requests:
        memory: "1Gi"
        cpu: "250m"
```

If overriding them, do consider the implications on the QoS class of the Pods and
cluster utilization.

## Volumes

The built-in Helm chart aims to provide a simple way to define and mount volumes in your
containers, much like you can in Docker Compose. The following example creates an empty
volume which is mounted in both containers.

```yaml
services:
  default:
    image: ubuntu:24.04
    command: ["tail", "-f", "/dev/null"]
    volumes:
      - "my-volume:/my-volume-mount-path"
  worker:
    image: ubuntu:24.04
    command: ["tail", "-f", "/dev/null"]
    volumes:
      - "my-volume:/my-volume-mount-path"
volumes:
  my-volume:
```

Note that this does not allow you to mount directories from the client system (your
machine) into the containers.

This requires that your cluster has a Storage Class named `nfs-csi` which supports the
`ReadWriteMany` access mode for `PersistentVolumeClaim`. See [Remote
Cluster](../getting-started/remote-cluster.md).

You can override the `spec` of the `PersistentVolumeClaim` to suit your needs.

```yaml
volumes:
  my-volume:
    spec:
      storageClassName: azurefile
      accessModes:
        - ReadWriteOnce
      resources:
        requests:
          storage: 1Gi
```

## Networks

By default, all Pods in a Kubernetes cluster can communicate with each other. Network
policies are used to restrict this communication. The built-in Helm chart restricts Pods
to only communicate with other Pods in the same Helm release.

Additional restrictions can be put in place, for example, to simulate networks within
your eval.

```yaml
services:
  default:
    image: ubuntu:24.04
    command: ["tail", "-f", "/dev/null"]
    networks:
      - a
  intermediate:
    image: ubuntu:24.04
    command: ["tail", "-f", "/dev/null"]
    dnsRecord: true
    networks:
      - a
      - b
  target:
    image: ubuntu:24.04
    command: ["tail", "-f", "/dev/null"]
    dnsRecord: true
    networks:
      - b
networks:
  a:
  b:
```

In the example above, the `default` service can communicate with the `intermediate`
service, but not the `target` service. The `intermediate` service can communicate with
the `target` service.

When no networks are specified all Pods within a Helm release can communicate with each
other. If any networks are specified, any Pod which does not specify a network will not
be able to communicate with any other Pod.

## Additional resources

You can pass arbitrary Kubernetes resources to the Helm chart using the
`additionalResources` key in the `values.yaml` file.

```yaml
additionalResources:
  - apiVersion: v1
    kind: Secret
    metadata:
      name: my-secret
    type: Opaque
    data:
      password: my-password
```

## Annotations

You can pass arbitrary annotations to the Helm chart using the top-level `annotations`
key in the `values.yaml` file. These will be added as annotations to the Pods, PVCs and
network policies.

The `k8s_sandbox` package automatically includes the Inspect task name as an annotation.

```yaml
annotations:
  my-annotation: my-value
```

This may be useful for determining which task a Pod belongs to.

## Render chart without installing

```sh
helm template ./resources/helm/agent-env > scratch/rendered.yaml
```

## Install chart manually { #manual-chart-install }

Normally, the Helm chart is installed by the `k8s_sandbox` package. However, you can
install it manually which might be useful for debugging.

```sh
helm install my-release ./resources/helm/agent-env -f path/to/helm-values.yaml
```

`my-release` is the release name. Consider using your name or initials.

`./resources/helm/agent-env` is the path to the Helm chart. If you are outside the
`k8s_sandbox` repository, pass the path to the chart in the `k8s_sandbox` repository, or
the path to the chart within your virtual environment's `k8s_package/` directory.

```sh
helm install my-release ~/repos/k8s_sandbox/resources/helm/agent-env -f ...
helm install my-release \
  .venv/lib/python3.12/site-packages/k8s_sandbox/resources/helm/agent-env -f ...
```

Remember to uninstall the release when you are done.

```sh
helm uninstall my-release
```

## Generate docs

You can regenerate docs using [helm-docs](https://github.com/norwoodj/helm-docs) after
changing the default `values.yaml`.

```sh
brew install norwoodj/tap/helm-docs
helm-docs ./resources/helm/agent-env
```

This is done automatically by the a pre-commit hook.

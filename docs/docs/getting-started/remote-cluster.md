# Remote Cluster

## Requirements

### If using the built-in Helm Chart

#### Cilium

Your cluster will need to have [Cilium](https://cilium.io/) installed.

If choosing to deploying any cluster- or namespace-wide Cilium Network Policies, please
consider their interplay with the CNPs that this package's built-in Helm chart deploys.
Cilium effectively combines policies by means of a logical disjunction (OR)
([docs](https://docs.cilium.io/en/latest/security/policy/intro/#rule-basics)) so if your
policies are too permissive, they may undermine the more restrictive policies deployed
by the built-in Helm chart. In particular, see the [DNS exfiltration
section](../security/network-access.md#dns-exfiltration).

#### `StorageClass`

To make use of the `volumes` functionality offered by the built-in Helm chart, your
cluster must have an `nfs-csi`
[StorageClass](https://kubernetes.io/docs/concepts/storage/storage-classes/) which
supports the `ReadWriteMany` access mode on `PersistentVolumeClaim`. If this is not
practical, you can override the `spec` field of any `volumes` in the `values.yaml` to
your choosing.

#### gVisor

Unless you override the `runtimeClassName` in your `values.yaml`, you will need to have
a `gvisor` [Runtime
Class](https://kubernetes.io/docs/concepts/containers/runtime-class/) available in your
cluster:

```yaml
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
handler: runsc
```

Read more about the rationale for using gVisor by default in [Container
Runtime](../security/container-runtime.md).

You might also wish to add a `runc` RuntimeClass in case you wish to disable gVisor for
certain Pods:
```yaml
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: runc
handler: runc
```

## Recommendations

Provide each user with their own namespace which is separate from system namespaces.

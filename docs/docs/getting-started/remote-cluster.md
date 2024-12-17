# Remote Cluster

## Requirements

### If using the built-in Helm Chart

Your cluster will need to have [Cilium](https://cilium.io/) installed.

To make use of the `volumes` functionality offered by the built-in Helm chart, your
cluster must have an `nfs-csi`
[StorageClass](https://kubernetes.io/docs/concepts/storage/storage-classes/) which
supports the `ReadWriteMany` access mode on `PersistentVolumeClaim`. If this is not
practical, you can override the `spec` field of any `volumes` in the `values.yaml` to
your choosing.

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

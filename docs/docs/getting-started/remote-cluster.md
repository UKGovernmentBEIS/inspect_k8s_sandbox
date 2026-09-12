# Remote Cluster

## Requirements

### On a Cilium cluster, with any chart

The identity running Inspect needs `get` and `list` on `ciliumendpoints.cilium.io` in
the namespace it installs into, whichever chart it installs. Kubernetes reports a pod
ready before Cilium has finished building its endpoint, so installation waits on those
endpoints before handing the sandbox back. Without the permission the wait is skipped
with a warning, and a sandbox's first commands can race their own egress rules: an
allowed domain may fail to resolve, or a pod with unrestricted egress may briefly have
no network. A cluster with no CiliumEndpoint CRD is not running Cilium and skips the
wait silently.

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

The chart's own policies are installed as Helm pre-install hooks, so that the policy
objects exist before the pods they protect rather than after them (Helm creates custom
resources last). Note what that does and does not guarantee: the objects are in the API
server before the pod is created, but not that every node's Cilium agent has imported
them before it builds the pod's endpoint. That import is the remaining window —
milliseconds, against the seconds a pod takes to be scheduled and started — and is why
the wait above exists as well.

Helm does not remove hook resources with the release, so this package deletes the
policies itself on uninstall. That needs `list` and `delete` on
`ciliumnetworkpolicies.cilium.io` in the namespace. `delete` was already needed, since
Helm deleted these objects itself while they were ordinary release resources; `list` is
a new requirement — an identity without it installs the chart normally and then cannot
clean up, failing the uninstall with `Failed to list the release's network policies`.
The policies are deleted one at a time rather than as a collection, so
`deletecollection` is not required. A release uninstalled by hand with `helm uninstall`
leaves its `CiliumNetworkPolicy` objects behind, which `kubectl delete cnp -l
app.kubernetes.io/instance=<release>` removes.

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

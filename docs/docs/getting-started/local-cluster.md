# Local Cluster

If you don't have access to a remote Kubernetes cluster, you can prototype locally.

## Devcontainer

This repository publishes a prebuilt [devcontainer](https://containers.dev/) image which you can use as a starting point, it is configured in  `.devcontainer/devcontainer.json`.

Note that the container **doesn't** include an installation of `inspect` or
`inspect_k8s_sandbox` - you should install these with whatever package management
system your project is using. The container handles the setup and configuration of
a local cluster using minikube.

## Self-Build

### Dependencies

* [minikube](https://minikube.sigs.k8s.io/docs/)
* [gVisor](https://gvisor.dev/docs/user_guide/install/)
* [Cilium](https://github.com/cilium/cilium-cli)

### Setup

A minimal setup compatible with the built-in Helm chart can be created as follows:

```sh
minikube start --container-runtime=containerd --addons=gvisor

kubectl apply -f - <<EOF
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: runc
handler: runc
EOF

kubectl apply -f - <<EOF
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nfs-csi
provisioner: k8s.io/minikube-hostpath
reclaimPolicy: Delete
volumeBindingMode: Immediate
EOF

cilium install
cilium status --wait
```

The `runc` `RuntimeClass` is required in order to specify a `runtimeClassName` of `runc`
in your `values.yaml` files (even if runc is the cluster's default).

You can see the available container runtime class names with:

```sh
kubectl get runtimeclass
```

The `nfs-csi` `StorageClass` is required in order to use the `volumes` functionality
offered by the built-in Helm chart. It actually uses the `minikube-hostpath`
provisioner.

!!! warning

    This is an example setup which is appropriate for development work, but
    should not be used long term or in a production setting. For long-term use
    you should use a larger, more resilient cluster with separate node groups
    for critical services.

If you wish to use images built locally or from a private registry, the quickest
approach may be to manually load them into minikube. There are other methods in the
[minikube documentation](https://minikube.sigs.k8s.io/docs/handbook/pushing/).

```sh
minikube image load <image-name>:<tag>
```

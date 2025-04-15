# Prerequisites

## Local environment

The [Helm CLI](https://helm.sh/docs/intro/install/) >=3.10.0 must be installed and on
the PATH of the environment in which Inspect in running.

```sh
helm version
```

### Recommended additional tools

* [K9s](https://k9scli.io/)
* [kubectl](https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/)

## Image requirements

The `k8s_sandbox` has been designed to make minimal assumptions about the images you
use. However, you will need the following commonly available binaries available in the
containers which you'd like `K8sSandboxEnvironment` to directly interact with (i.e.
`exec()`, `read_file()`, `write_file()`):

* `sh`
* `sync`
* `echo`
* `head`
* `cat`
* `mkdir`
* `timeout`
* `base64`
* `runuser`

## Cluster requirements

You must have access to a K8s cluster. For a remote cluster, see the [Remote
Cluster](remote-cluster.md) for the requirements.

See the [Minikube](local-cluster.md) documentation for a minimal local setup.

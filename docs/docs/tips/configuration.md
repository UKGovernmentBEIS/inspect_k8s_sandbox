# Advanced Configuration

## Helm install timeout { #helm-install-timeout }

The built-in Helm install timeout is 10 minutes. If you're running large eval sets and
expect to run into cluster capacity issues, you can increase the timeout by setting the
`INSPECT_HELM_TIMEOUT` environment variable to a number of seconds.

```sh
export INSPECT_HELM_TIMEOUT=21600   # 6 hours
```


## Helm release labels { #helm-release-labels }

You can add custom labels to Helm releases by setting the `INSPECT_HELM_LABELS`
environment variable to a comma-separated list of `key=value` pairs. These are added as
Helm release labels alongside the default `inspectSandbox=true` label, and are queryable
via `helm list --selector`.

```sh
export INSPECT_HELM_LABELS="ci-branch=my-feature,run-id=42"
```


## Targeting specific or multiple kubeconfig contexts

Your
[kubeconfig](https://kubernetes.io/docs/concepts/configuration/organize-cluster-access-kubeconfig/)
file provides information about the Kubernetes clusters you can access.

By default, your kubeconfig's _current context_ is used to install Helm charts. You can
determine what this is by running:

```bash
kubectl config current-context
```

At an Inspect `Task` or `Sample` level, you can specify the name of the Kubernetes
context in which the Helm chart should be installed by providing a
`K8sSandboxEnvironmentConfig` in the `sandbox` argument. This might be useful if for
example certain Samples require GPU nodes which are only available in a specific
cluster.

```python
Sample(
    sandbox=SandboxEnvironmentSpec(
        "k8s",
        K8sSandboxEnvironmentConfig(context="minikube"),
    ),
)
```

!!! note
    If using the `inspect sandbox cleanup k8s` [command](cleanup.md), please note
    that it only cleans up Helm releases in the current context. Use `kubectl` or `k9s`
    to clean up Helm releases in other contexts.


## Structured logging truncation threshold

By default, each key/value pair (e.g. an exec command's output) logged to Python's
`logging` module (via structured JSON logging) is truncated to 1000 characters. This is
to prevent logs from becoming excessively large when e.g. a model runs a command which
produces a large amount of output. This can be adjusted by setting the
`INSPECT_LOG_TRUNCATION_THRESHOLD` environment variable to a number of characters.

```sh
export INSPECT_LOG_TRUNCATION_THRESHOLD=5000
```

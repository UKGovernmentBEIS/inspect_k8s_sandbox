# Concurrency

## Helm install and uninstall operations

To avoid overwhelming the Kubernetes API server, the `k8s_sandbox` package limits the
number of concurrent `helm install` operations to 8 and, independently, the number of
`helm uninstall` operations to 8.

Critically, there is a separate semaphore for installs and uninstalls. This prevents
deadlocks if the cluster capacity has been reached.

Inspect's console output shows the number of install and uninstall operations currently
in progress.

The maximum values can be configured by setting the `INSPECT_MAX_HELM_INSTALL` and
`INSPECT_MAX_HELM_UNINSTALL` environment variables.

```sh
export INSPECT_MAX_HELM_INSTALL=10
export INSPECT_MAX_HELM_UNINSTALL=10
```

Do consider the effect of increasing these values on the Kubernetes API server.

## Pod operations

A pod-op is an operation that is performed on a Pod, such as `SandboxEnvironment`'s
`exec()`, `read_file()`, `write_file()`. By default, this is limited on the client (i.e.
the machine running the Inspect process) to CPU count * 4. You can adjust this by
setting the `INSPECT_MAX_POD_OPS` environment variable.

```sh
export INSPECT_MAX_POD_OPS=100
```

These operations are typically I/O bound (from the client's perspective). Do bear in
mind that these operations are routed through the Kubernetes API server.

Inspect's console output shows the number of Pod operations currently in progress.

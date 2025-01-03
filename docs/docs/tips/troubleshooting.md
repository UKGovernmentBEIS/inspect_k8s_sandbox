# Troubleshooting

For general K8s and Inspect sandbox debugging, see the [Debugging K8s
Sandboxes](debugging-k8s-sandboxes.md) guide.

## View Inspect's `TRACE`-level logs

A good starting point to many issues is to view the `TRACE`-level logs written by
Inspect. See the [`TRACE` log level
section](debugging-k8s-sandboxes.md#trace-log-level).

## I'm seeing "Helm install: context deadline exceeded" errors

This means that the Helm chart installation timed out. When installing the Helm chart,
the `k8s_sandbox` package uses the `--wait` flag to wait for all Pods to be ready.

Therefore, this error can be an indication of:

* Cluster capacity issues. Consider [increasing the
  timeout](configuration.md#helm-install-timeout) or scaling up your cluster.
* A Pod failing to enter the ready state (could be a failing readiness probe, failing to
  pull the image, crash loop backoff, etc.)

Try installing the chart again (this can also be [done
manually](../helm/built-in-chart.md#manual-chart-install)) and check the Pod statuses
and logs using a tool like K9s. Use the helm release name (will be in error message) to
filter the Pods.

## I'm seeing "Helm uninstall failed" errors

These are likely because the Helm chart was never installed. This typically happens if
you cancel an eval, or an eval fails before a certain sample's Helm chart was installed
(including if the chart installation failed).

Check to see if any Helm releases were left behind:

```sh
helm list
```

And if you wish to uninstall them:

```sh
helm uninstall <release-name>
```

## I'm seeing "Handshake status 404 Not Found" errors from Pod operations

This typically indicates that the Pod has been killed. This may be due to:

* cluster issues (see [View cluster events](#view-cluster-events))
* because the eval had already failed for an unrelated reason and the Helm releases were
  uninstalled whilst some operations were queued or in flight. Check the `.json` or
  `.eval` log produced by Inspect to see the underlying error.

## View cluster events

Certain cluster events may impact your eval, for example, a node failure.

The following commands are a primitive way to view cluster events. Your cluster may have
observability tools which collect these events and provide a more user-friendly
interface.

```sh
kubectl get events --sort-by='.metadata.creationTimestamp'
```

To also see timestamps:

```sh
kubectl get events --sort-by='.metadata.creationTimestamp' \
  -o custom-columns=LastSeen:.lastTimestamp,Type:.type,Object:.involvedObject.name,Reason:.reason,Message:.message
```

To filter to a particular release or Pod, either pipe into `grep` or use the
`--field-selector` flag:

```sh
kubectl get events --sort-by='.metadata.creationTimestamp' \
  --field-selector involvedObject.name=agent-env-xxxxxxxx-default-0
```

Find the Pod name (including the random 8-character identifier) in the `TRACE`-level
logs or the stack trace.

To specify a namespace other than the default, use the `-n` flag.

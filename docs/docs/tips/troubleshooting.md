# Troubleshooting

For general K8s and Inspect sandbox debugging, see the [Debugging K8s
Sandboxes](debugging-k8s-sandboxes.md) guide.

## View Inspect's `TRACE`-level logs

A good starting point to many issues is to view the `TRACE`-level logs written by
Inspect. See the [`TRACE` log level
section](debugging-k8s-sandboxes.md#trace-log-level).

## I'm seeing "Helm install: context deadline exceeded" errors {#helm-context-deadline-exceeded}

This means that the Helm chart installation timed out. When installing the Helm chart,
the `k8s_sandbox` package uses the `--wait` flag to wait for all Pods to be ready.

Therefore, this error can be an indication of:

* If you have an auto-scaling cluster, it may need more time to provision new nodes.
* If you don't have an auto-scaling cluster, you may have reached capacity.
* If you are using large images, they may take a long time to pull onto the nodes.
* A Pod failing to enter the ready state (could be a failing readiness probe, failing to
  pull the image, crash loop backoff, etc.)

Consider [increasing the timeout](configuration.md#helm-install-timeout).

If your cluster does not auto-scale and it is at capacity, consider reducing parallelism
or scaling up the relevant cluster node group.

Try installing the chart again (this can also be [done
manually](../helm/built-in-chart.md#manual-chart-install)) and check the Pod statuses
and events using a tool like kubectl or K9s to get a definitive answer as to the
underlying problem. Use the Helm release name (will be in error message) to filter the
Pods.

## I'm seeing "Helm uninstall failed" errors

The `k8s_sandbox` package ignores "release not found" errors when uninstalling Helm
releases because they are expected when the Helm release was not successfully installed
(including when the user cancelled the eval).

Other uninstall failures (e.g. "failed to delete release") will result in an error.

Check to see if any Helm releases were left behind:

```sh
helm list
```

And if you wish to uninstall them:

```sh
helm uninstall <release-name>
```

If you wish to bulk uninstall all Inspect Helm charts, see the [cleanup
command](cleanup.md).

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

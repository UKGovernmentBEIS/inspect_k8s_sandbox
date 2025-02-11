# Cleanup

If the Inspect process were to terminate unexpectedly, it may leave behind resources in
the Kubernetes cluster. To see if any Helm releases have been left behind, either use
K9s and type `:helm` followed by enter, or use the Helm CLI:

```sh
helm list
```

To uninstall all of the Inspect-managed Helm releases:

```sh
inspect sandbox cleanup k8s
```

This will list all of the Inspect-managed Helm releases (it will infer whether they are
Inspect-managed based on the labels) in the current namespace and offer to uninstall
them all for you.

!!! warning
    This command will find and uninstall all Inspect-managed Helm releases **for any
    user of the Kubernetes namespace**. If you are using a shared Kubernetes namespace,
    please be careful when choosing which Helm releases to uninstall.

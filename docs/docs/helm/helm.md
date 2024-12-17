# What is Helm?

[Helm](https://helm.sh/) charts are used to deploy the sandbox environments. Under the
hood, they generate Kubernetes manifests (YAML files) which are applied (installed) to
your cluster.

There is a [built-in chart](built-in-chart.md) included with the `k8s_sandbox` package.
You can also [create your own Helm chart](custom-chart.md).

Helm releases are installed in the namespace specified by the `current-context` of your
[kubeconfig](https://kubernetes.io/docs/concepts/configuration/organize-cluster-access-kubeconfig/).

Helm is used so that researchers can write a simple `helm-values.yaml` file which Helm
_renders_ into a set of Kubernetes resources using the templates defined in the chart.
This is to abstract away the complexity of writing Kubernetes resources in YAML for
those who are not familiar with it.

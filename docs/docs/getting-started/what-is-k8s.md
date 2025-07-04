# What is K8s?

This is a very high level overview of the aspects of Kubernetes (a.k.a. K8s) which are
particularly salient to the running of Inspect evals.

Kubernetes is a _container_ orchestration platform. Containers are typically Docker
containers.

A Kubernetes _cluster_ consists a set of many _nodes_ which run containerised
applications. A node is a VM or physical machine. A _Pod_ houses one or more
containers on a node. There can be many Pods on a node.

A _namespace_ is a way to segregate resources in a cluster.

_Helm_ is a package manager for Kubernetes. It is used to install _charts_ which are
bundles of Kubernetes resources. Helm creates _releases_ which are instances of charts.
We typically install one Helm release per eval sample (one for every epoch too).

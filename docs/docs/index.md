# Introduction

![pkg icon](assets/icon-dark.png){: style="height:80px;float:left;margin-right:20px;"}
The `k8s_sandbox` Python package provides a Kubernetes (K8s) sandbox environment for
[inspect_ai](https://inspect.aisi.org.uk/).

<br>
Learn more about what sandbox environments are for from the [Inspect docs
site](https://inspect.aisi.org.uk/tools.html#sandboxing). At a high level, this package
lets you run Docker containers which your agents interact with within a Kubernetes
cluster instead of locally (e.g. using Docker Compose).

The Inspect process itself still runs on your local machine.

## Why use Kubernetes over Docker Compose?

* **Scalability**: A typical Kubernetes cluster is distributed across multiple nodes,
  allowing you to deploy many containers at once and run your evals at a much larger
  scale.
* **Security**: Leverage [Cilium](https://cilium.io/) Network Policies to provide
  fine-grained internet access control. Use [gVisor](https://gvisor.dev/)[^1] to run
  containers in a sandboxed manner.

* **Tooling**: Kubernetes has a rich ecosystem of tools and services which can be used
  to monitor and debug your containers such as [K9s](https://k9scli.io/).


## About

[![AISI Logo](assets/aisi-logo.svg){: style="height:60px;"}](https://www.aisi.gov.uk/)

Created by the [UK AI Security Institute](https://aisi.gov.uk/).

[^1]: gVisor can also be used in Docker Compose, but is enabled by default with the
    `k8s_sandbox` package.

This devcontainer include tools such as minikube and Helm which are required for
Kubernetes Sandbox Environment development and testing.

It is used by the CI pipeline. Its use is optional for local development.

The minikube profile is named after the checkout directory (see `post-create.sh`), so
several checkouts can run devcontainers on one host. Bare `minikube` commands need
`-p <profile>` or `export MINIKUBE_PROFILE=<profile>`; `kubectl` and the tests use the
kube context and need nothing. The cluster is restarted automatically when the
container starts, e.g. after a host reboot.

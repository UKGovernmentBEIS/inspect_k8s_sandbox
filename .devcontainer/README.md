This devcontainer include tools such as minikube and Helm which are required for
Kubernetes Sandbox Environment development and testing.

It is used by the CI pipeline. Its use is optional for local development.

The minikube profile is named after the checkout directory (lower-cased, non-alphanumerics
replaced with `-`, cut to 63 characters; see `post-create.sh`), so several checkouts can
run devcontainers on one host. Each checkout gets its own `MINIKUBE_HOME` under
`~/.minikube` (see `devcontainer.json`) with that profile set as active, so `minikube`,
`kubectl` and the tests all target this checkout's cluster with no extra flags. The
cluster is restarted automatically when the container starts, e.g. after a host reboot.

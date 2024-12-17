# Future Work

## Automatically running from `compose.yaml` files

The typical small-scale community user will likely write agentic evals using the
Docker Compose sandbox environment provider. But there will be appetite within larger
organisations to run these evals in a Kubernetes cluster - either for scalability or
security reasons.

To avoid maintaining both `compose.yaml` and `helm-values.yaml` files, we are
considering writing adding support for generating a `helm-values.yaml` file on the fly
from a `compose.yaml` file. Only very simple `compose.yaml` files would be supported.

Features such as automatically building docker images from a Dockerfile would not be
supported.

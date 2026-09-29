# Runs on every container start. Brings the cluster back after a host/docker restart,
# which leaves the minikube container stopped (its restart policy is "no").
set -e

# One minikube profile per checkout; see post-create.sh.
export MINIKUBE_PROFILE
MINIKUBE_PROFILE=$(basename "$PWD" | tr -c 'A-Za-z0-9-\n' '-')

set +e
minikube status >/dev/null 2>&1
status=$?
set -e
case $status in
  0) exit 0 ;;
  85) echo "No minikube profile '${MINIKUBE_PROFILE}'; run 'bash .devcontainer/post-create.sh' to create the cluster."; exit 0 ;;
esac

echo "Starting minikube profile '${MINIKUBE_PROFILE}'..."
minikube start

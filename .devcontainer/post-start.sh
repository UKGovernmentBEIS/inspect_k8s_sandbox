# Runs on every container start. Brings the cluster back after a host/docker restart,
# which leaves the minikube container stopped (its restart policy is "no").
# post-create.sh makes this checkout's profile the active one, so no -p is needed.
set -e

set +e
minikube status >/dev/null 2>&1
status=$?
set -e
case $status in
  0) exit 0 ;;
  85) echo "No minikube cluster for this checkout; run 'bash .devcontainer/post-create.sh' to create one."; exit 0 ;;
esac

echo "Starting minikube..."
minikube start

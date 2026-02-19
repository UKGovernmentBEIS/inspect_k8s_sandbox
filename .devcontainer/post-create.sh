# Exit immediately if a command exits with a non-zero status, and print each command.
set -e -x
exec > >(tee /tmp/post-create.log) 2>&1

echo "Setting up Minikube..."
minikube delete
# github actions runner has 2 cpus, 8G memory
minikube start --addons=gvisor --cni bridge --container-runtime=containerd --memory=4g

# Add the containerd RuntimeClass to the cluster.
kubectl apply -f - <<EOF
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: runc
handler: runc
EOF

# Add a mocked nfs-csi StorageClass which uses the hostpath provisioner.
kubectl apply -f - <<EOF
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nfs-csi
provisioner: k8s.io/minikube-hostpath
reclaimPolicy: Delete
volumeBindingMode: Immediate
EOF

# Install Cilium CLI. Previously, the ghcr.io/audacioustux/devcontainers/cilium:1
# devcontainer feature was used, but it doesn't allow a specific version of the CLI to
# be installed and the latest version failed at `cilium install`.
CILIUM_CLI_VERSION=v0.16.15
CILIUM_CLI_ARCH=amd64
echo "Installing Cilium CLI $CILIUM_CLI_VERSION $CILIUM_CLI_ARCH..."
curl -L --fail --remote-name-all https://github.com/cilium/cilium-cli/releases/download/${CILIUM_CLI_VERSION}/cilium-linux-${CILIUM_CLI_ARCH}.tar.gz{,.sha256sum}
sha256sum --check cilium-linux-${CILIUM_CLI_ARCH}.tar.gz.sha256sum
sudo tar xzvfC cilium-linux-${CILIUM_CLI_ARCH}.tar.gz /usr/local/bin
rm cilium-linux-${CILIUM_CLI_ARCH}.tar.gz{,.sha256sum}

echo "Installing Cilium..."
cilium install
cilium status --wait
cilium hubble enable --ui

echo "Installing uv environment..."
uv sync --extra dev

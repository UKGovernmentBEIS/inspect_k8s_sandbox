# Exit immediately if a command exits with a non-zero status, and print each command.
set -e -x

echo "Setting up Minikube..."
minikube delete || true
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

echo "Installing Cilium..."
cilium install
cilium status --wait
cilium hubble enable --ui

echo "Installing uv environment..."
uv sync --extra dev

# Exit immediately if a command exits with a non-zero status, and print each command.
set -e -x

CLUSTER=true
for arg in "$@"; do
  case "$arg" in
    --no-cluster) CLUSTER=false ;;
  esac
done

if [ "$CLUSTER" = true ]; then
  # One minikube profile per checkout, so several devcontainers on one host don't fight
  # over the default "minikube" cluster. The name is also the node container's hostname,
  # the kube context and, through that, Cilium's cluster name (lower case, 32 characters
  # max), and must satisfy minikube's profile regex (alphanumerics and dashes, starting
  # and ending with an alphanumeric). Long names keep a prefix plus a hash of the whole
  # name, so two long checkouts can't collide.
  MINIKUBE_PROFILE=$(basename "$PWD" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9-\n' '-' | sed -E 's/^-+//; s/-+$//')
  if [ ${#MINIKUBE_PROFILE} -gt 32 ]; then
    MINIKUBE_PROFILE="${MINIKUBE_PROFILE:0:24}-$(printf %s "$MINIKUBE_PROFILE" | sha256sum | cut -c1-7)"
  fi
  export MINIKUBE_PROFILE
  echo "Setting up Minikube profile '${MINIKUBE_PROFILE}'..."
  minikube delete
  # github actions runner has 2 cpus, 8G memory
  minikube start --cni bridge --container-runtime=containerd --memory=4g
  # Record it as the active profile in this checkout's MINIKUBE_HOME, so post-start.sh
  # and bare `minikube` commands need no -p.
  minikube profile "$MINIKUBE_PROFILE"

  # gVisor is installed by hand rather than with the minikube addon. The addon downloads
  # runsc from a URL that now 404s and re-appends its containerd config every time the
  # node restarts, which breaks containerd (kubernetes/minikube#23709). Everything below
  # lands in the node container's filesystem and survives restarts.
  GVISOR_RELEASE=20260817.0
  for gvisor_binary in runsc containerd-shim-runsc-v1; do
    echo "Installing gVisor $GVISOR_RELEASE $gvisor_binary..."
    curl -L --fail -o "$gvisor_binary" \
      "https://storage.googleapis.com/gvisor/releases/release/${GVISOR_RELEASE}/x86_64/${gvisor_binary}"
    minikube cp "$gvisor_binary" "/usr/bin/${gvisor_binary}"
    rm "$gvisor_binary"
  done
  minikube cp .devcontainer/gvisor-containerd.toml /tmp/gvisor-containerd.toml
  # minikube cp doesn't preserve the executable bit
  minikube ssh -- sudo chmod 755 /usr/bin/runsc /usr/bin/containerd-shim-runsc-v1
  minikube ssh -- "sudo sh -c 'cat /tmp/gvisor-containerd.toml >> /etc/containerd/config.toml && systemctl restart containerd'"

  kubectl apply -f - <<EOF
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
handler: runsc
---
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
  CILIUM_CLI_VERSION=v0.18.8
  CILIUM_CLI_ARCH=amd64
  echo "Installing Cilium CLI $CILIUM_CLI_VERSION $CILIUM_CLI_ARCH..."
  curl -L --fail --remote-name-all https://github.com/cilium/cilium-cli/releases/download/${CILIUM_CLI_VERSION}/cilium-linux-${CILIUM_CLI_ARCH}.tar.gz{,.sha256sum}
  sha256sum --check cilium-linux-${CILIUM_CLI_ARCH}.tar.gz.sha256sum
  mkdir -p ~/.local/bin
  tar xzvfC cilium-linux-${CILIUM_CLI_ARCH}.tar.gz ~/.local/bin
  rm cilium-linux-${CILIUM_CLI_ARCH}.tar.gz{,.sha256sum}

  echo "Installing Cilium..."
  ~/.local/bin/cilium install
  ~/.local/bin/cilium status --wait
  ~/.local/bin/cilium hubble enable --ui
else
  echo "Skipping cluster setup (--no-cluster)"
fi

# Everything below runs regardless of --no-cluster.
echo "Installing uv environment..."
uv sync --extra dev --frozen

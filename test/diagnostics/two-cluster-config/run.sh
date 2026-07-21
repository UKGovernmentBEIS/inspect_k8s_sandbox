#!/usr/bin/env bash
# E2E two-cluster test for PR #177: kubeconfig-first config loading.
#
# Creates two minikube clusters (runner + sandbox), builds a test image,
# and runs an Inspect eval inside a runner pod that must reach the sandbox
# cluster via a mounted kubeconfig.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

RUNNER_PROFILE=runner
SANDBOX_PROFILE=sandbox
IMAGE_NAME=e2e-runner:latest
JOB_NAME=e2e-two-cluster
KUBECONFIG_TMP=/tmp/sandbox-kubeconfig
TIMEOUT_SECONDS=300

# ── Cleanup on exit ──────────────────────────────────────────────────────
cleanup() {
    echo "Cleaning up minikube clusters..."
    minikube delete -p "$RUNNER_PROFILE" 2>/dev/null || true
    minikube delete -p "$SANDBOX_PROFILE" 2>/dev/null || true
    rm -f "$KUBECONFIG_TMP"
}
trap cleanup EXIT

# ── 1. Prereqs ───────────────────────────────────────────────────────────
echo "Checking prerequisites..."
for cmd in minikube docker kubectl cilium; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: $cmd not found on PATH" >&2
        exit 1
    fi
done

# ── 2. Create runner cluster ─────────────────────────────────────────────
echo "Creating runner cluster..."
minikube start -p "$RUNNER_PROFILE" \
    --driver=docker \
    --cni=bridge \
    --container-runtime=containerd \
    --memory=2g

# ── 3. Create sandbox cluster ────────────────────────────────────────────
echo "Creating sandbox cluster..."
minikube start -p "$SANDBOX_PROFILE" \
    --driver=docker \
    --cni=bridge \
    --container-runtime=containerd \
    --memory=6g

# Apply runc RuntimeClass
kubectl --context "$SANDBOX_PROFILE" apply -f - <<'EOF'
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: runc
handler: runc
EOF

# Apply mocked nfs-csi StorageClass
kubectl --context "$SANDBOX_PROFILE" apply -f - <<'EOF'
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nfs-csi
provisioner: k8s.io/minikube-hostpath
reclaimPolicy: Delete
volumeBindingMode: Immediate
EOF

# Install Cilium (required for CiliumNetworkPolicy CRDs used by the agent-env chart).
# We only need the CRDs registered, not a fully healthy Cilium data plane.
echo "Installing Cilium on sandbox cluster..."
cilium install --context "$SANDBOX_PROFILE"

echo "Waiting for CiliumNetworkPolicy CRD to be registered..."
for i in $(seq 1 60); do
    if kubectl --context "$SANDBOX_PROFILE" get crd ciliumnetworkpolicies.cilium.io &>/dev/null; then
        echo "CiliumNetworkPolicy CRD is available."
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "ERROR: Timed out waiting for CiliumNetworkPolicy CRD" >&2
        exit 1
    fi
    sleep 2
done

# ── 4. Connect Docker networks and generate kubeconfig ───────────────────
# minikube --driver=docker creates a separate Docker network per profile.
# Connect runner to the sandbox network so pods in runner can reach sandbox's
# API server (pod traffic is NATed through the runner container).
echo "Connecting runner to sandbox Docker network..."
docker network connect "$SANDBOX_PROFILE" "$RUNNER_PROFILE"

echo "Generating sandbox kubeconfig..."
SANDBOX_IP=$(minikube ip -p "$SANDBOX_PROFILE")
kubectl config view --flatten --minify --context "$SANDBOX_PROFILE" \
    | sed "s|https://127\.0\.0\.1:[0-9]*|https://${SANDBOX_IP}:8443|g" \
    > "$KUBECONFIG_TMP"

echo "Sandbox kubeconfig server: https://${SANDBOX_IP}:8443"

# ── 5. Build test image ─────────────────────────────────────────────────
echo "Building test image..."
docker build -t "$IMAGE_NAME" -f "$SCRIPT_DIR/Dockerfile" "$REPO_ROOT"

# ── 6. Load image into runner cluster ────────────────────────────────────
echo "Loading image into runner cluster..."
minikube -p "$RUNNER_PROFILE" image load "$IMAGE_NAME"

# ── 7. Create kubeconfig Secret in runner ────────────────────────────────
echo "Creating kubeconfig secret in runner cluster..."
kubectl --context "$RUNNER_PROFILE" create secret generic sandbox-kubeconfig \
    --from-file=config="$KUBECONFIG_TMP"

# ── 8. Run Job in runner cluster ─────────────────────────────────────────
echo "Creating test Job in runner cluster..."
kubectl --context "$RUNNER_PROFILE" apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: $JOB_NAME
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: test
        image: $IMAGE_NAME
        imagePullPolicy: Never
        command: ["python", "/app/test/diagnostics/two-cluster-config/test_eval.py"]
        env:
        - name: KUBECONFIG
          value: /home/appuser/.kube/config
        - name: INSPECT_HELM_TIMEOUT
          value: "120"
        volumeMounts:
        - name: kubeconfig
          mountPath: /home/appuser/.kube
          readOnly: true
      volumes:
      - name: kubeconfig
        secret:
          secretName: sandbox-kubeconfig
EOF

# ── 9. Wait for Job ─────────────────────────────────────────────────────
echo "Waiting for Job to complete (timeout: ${TIMEOUT_SECONDS}s)..."
status=""
deadline=$((SECONDS + TIMEOUT_SECONDS))
while [ $SECONDS -lt $deadline ]; do
    # Check all condition types (a job can have Complete or Failed)
    conditions=$(kubectl --context "$RUNNER_PROFILE" get job "$JOB_NAME" \
        -o jsonpath='{.status.conditions[*].type}' 2>/dev/null || true)
    if echo "$conditions" | grep -q "Complete"; then
        status="Complete"
        break
    elif echo "$conditions" | grep -q "Failed"; then
        status="Failed"
        break
    fi
    sleep 5
done

# ── 10. Print logs and report ────────────────────────────────────────────
echo ""
echo "========================================"
echo "Job logs:"
echo "========================================"
POD=$(kubectl --context "$RUNNER_PROFILE" get pods -l job-name="$JOB_NAME" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -n "$POD" ]; then
    kubectl --context "$RUNNER_PROFILE" logs "$POD" || true
fi

echo ""
echo "========================================"
if [ "$status" = "Complete" ]; then
    echo "RESULT: PASS"
    exit 0
elif [ "$status" = "Failed" ]; then
    echo "RESULT: FAIL"
    exit 1
else
    echo "RESULT: TIMEOUT (job did not complete within ${TIMEOUT_SECONDS}s)"
    # Print pod status for debugging
    kubectl --context "$RUNNER_PROFILE" describe job "$JOB_NAME" || true
    exit 1
fi

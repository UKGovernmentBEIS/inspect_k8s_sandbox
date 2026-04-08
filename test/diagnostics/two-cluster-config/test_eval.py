"""E2E test: run an Inspect eval inside a pod to verify kubeconfig-first loading.

This script is self-contained (no imports from the test package) and runs inside
a Kubernetes pod in the "runner" cluster, with KUBECONFIG pointing at the
"sandbox" cluster. It validates that k8s_sandbox prefers the kubeconfig over
in-cluster config (the fix from PR #177).
"""

from __future__ import annotations

import sys
from pathlib import Path

from inspect_ai import Task, eval
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import ModelOutput, get_model
from inspect_ai.scorer import match
from inspect_ai.solver import generate, use_tools
from inspect_ai.tool import bash
from inspect_ai.util import SandboxEnvironmentSpec

from k8s_sandbox import K8sSandboxEnvironmentConfig
from k8s_sandbox._kubernetes_api import _Config

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * 60)
    print("E2E two-cluster test: kubeconfig-first config loading")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. Check that kubeconfig was preferred over in-cluster config
    # ------------------------------------------------------------------
    cfg = _Config.get_instance()
    print(f"_Config.in_cluster = {cfg.in_cluster}")
    if cfg.in_cluster:
        print("FAIL: in-cluster config was loaded instead of kubeconfig.")
        print("This means load_incluster_config() took priority — the PR #177 fix is not applied.")
        sys.exit(1)
    print("PASS: kubeconfig was preferred over in-cluster config.")

    # ------------------------------------------------------------------
    # 2. Run a minimal Inspect eval through k8s sandbox
    # ------------------------------------------------------------------
    model = get_model(
        "mockllm/model",
        custom_outputs=[
            ModelOutput.for_tool_call(
                model="mockllm/model",
                tool_name="bash",
                tool_arguments={"cmd": "echo hello"},
            ),
            ModelOutput.from_content(model="mockllm/model", content="hello"),
        ],
    )

    values_path = Path("/app/test/diagnostics/two-cluster-config/values.yaml")
    sandbox_config = K8sSandboxEnvironmentConfig(values=values_path)

    task = Task(
        dataset=MemoryDataset(
            samples=[Sample(input="Run a test command.", target="hello")]
        ),
        solver=[use_tools([bash(timeout=30)]), generate()],
        sandbox=SandboxEnvironmentSpec("k8s", sandbox_config),
        name="e2e-two-cluster",
        scorer=match(),
        max_messages=10,
    )

    print("\nRunning Inspect eval...")
    logs = eval(task, model=model, log_dir="/tmp/inspect-logs")
    log = logs[0]

    print(f"Eval status: {log.status}")
    if log.status != "success":
        print(f"FAIL: eval did not succeed. Error: {log.error}")
        sys.exit(1)

    if not log.samples:
        print("FAIL: no samples in eval log.")
        sys.exit(1)

    sample = log.samples[0]
    print(f"Sample scores: {sample.scores}")

    print("\n" + "=" * 60)
    print("PASS: eval completed successfully via kubeconfig-targeted sandbox cluster.")
    print("=" * 60)


if __name__ == "__main__":
    main()

"""Integration tests for behaviour after the sandbox pod has been replaced.

These exercise the full path: `K8sSandboxEnvironment.exec` → tenacity retry →
`Pod.exec` → `check_for_pod_restart` → identity refresh. The unit tests in
`test/k8s_sandbox/pod/test_check_for_pod_restart.py` cover those layers in
isolation; this module guards against the integration-level regression the
PR description calls out (callers looping on a stale UID).

Each test starts a sandbox, deletes the underlying pod from inside the solver
so the StatefulSet recreates it with a new UID, and then exercises
`sandbox.exec` against the new pod.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from inspect_ai import Task, eval
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox
from kubernetes import client  # type: ignore
from kubernetes import config as kube_config  # type: ignore

from k8s_sandbox import (
    K8sSandboxEnvironmentConfig,
    PodReplacedError,
)

pytestmark = pytest.mark.req_k8s


def _replace_pod_sync(
    name: str, namespace: str, context_name: str | None, old_uid: str
) -> str:
    """Delete the pod and block until the StatefulSet brings up a Ready replacement."""
    kube_config.load_kube_config(context=context_name)
    core = client.CoreV1Api()
    core.delete_namespaced_pod(name=name, namespace=namespace, grace_period_seconds=0)
    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline:
        try:
            pod = core.read_namespaced_pod(name=name, namespace=namespace)
        except client.ApiException as e:
            if e.status != 404:
                raise
            time.sleep(1.0)
            continue
        if pod.metadata.uid == old_uid:
            time.sleep(1.0)
            continue
        statuses = pod.status.container_statuses if pod.status else None
        ready = bool(statuses) and all(cs.ready for cs in statuses)
        if pod.status and pod.status.phase == "Running" and ready:
            return pod.metadata.uid
        time.sleep(1.0)
    raise TimeoutError(
        f"Pod {namespace}/{name} did not reach Ready with a new UID in time"
    )


@solver
def _replacement_probe() -> Solver:
    """Runs exec, replaces the pod, runs exec twice more, records outcomes."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        sb = sandbox()
        info = sb._sandbox._pod.info  # type: ignore[attr-defined]

        before = await sb.exec(["echo", "before"])
        assert before.success and before.stdout.strip() == "before"

        original_uid = info.uid
        new_uid = await asyncio.to_thread(
            _replace_pod_sync,
            info.name,
            info.namespace,
            info.context_name,
            original_uid,
        )

        first_exc_type: str | None = None
        first_cause_is_pod_replaced: bool = False
        first_stdout: str | None = None
        try:
            r1 = await sb.exec(["echo", "after-1"])
            first_stdout = r1.stdout
        except BaseException as e:
            first_exc_type = type(e).__name__
            first_cause_is_pod_replaced = isinstance(
                getattr(e, "__cause__", None), PodReplacedError
            )

        r2 = await sb.exec(["echo", "after-2"])

        state.metadata["probe"] = {
            "original_uid": original_uid,
            "new_uid": new_uid,
            "first_exc_type": first_exc_type,
            "first_cause_is_pod_replaced": first_cause_is_pod_replaced,
            "first_stdout": first_stdout,
            "second_success": r2.success,
            "second_stdout": r2.stdout,
        }
        return state

    return solve


def _run_probe(behavior: str) -> dict:
    task = Task(
        dataset=MemoryDataset(samples=[Sample(input="", target="")]),
        solver=_replacement_probe(),
        sandbox=(
            "k8s",
            K8sSandboxEnvironmentConfig(restarted_container_behavior=behavior),  # type: ignore[arg-type]
        ),
    )
    logs = eval(task, model="mockllm/model")
    log = logs[0]
    assert log.status == "success", f"eval failed: {log.error}"
    assert log.samples is not None and log.samples[0].metadata is not None
    return log.samples[0].metadata["probe"]


def test_warn_mode_silently_recovers_from_pod_replacement() -> None:
    """Default `warn` mode: pod replacement is invisible to the caller; both
    post-replacement execs succeed against the new pod."""
    probe = _run_probe("warn")
    assert probe["original_uid"] != probe["new_uid"]
    assert probe["first_exc_type"] is None, (
        f"warn mode should not raise; got {probe['first_exc_type']}"
    )
    assert probe["first_stdout"] is not None
    assert probe["first_stdout"].strip() == "after-1"
    assert probe["second_success"]
    assert probe["second_stdout"].strip() == "after-2"


def test_raise_mode_raises_then_recovers_after_pod_replacement() -> None:
    """`raise` mode: first post-replacement exec raises a K8sError wrapping
    PodReplacedError; the second exec succeeds because the cached identity was
    refreshed during the first raise."""
    probe = _run_probe("raise")
    assert probe["original_uid"] != probe["new_uid"]
    assert probe["first_exc_type"] is not None, (
        "raise mode should raise on the first exec after replacement"
    )
    assert probe["first_cause_is_pod_replaced"], (
        f"expected __cause__ to be PodReplacedError; "
        f"got {probe['first_exc_type']} with no PodReplacedError cause"
    )
    assert probe["second_success"]
    assert probe["second_stdout"].strip() == "after-2"

from unittest.mock import patch

import pytest
from inspect_ai.util import ExecResult
from kubernetes.client.exceptions import ApiException

from k8s_sandbox._pod.error import ContainerRestartedError, PodReplacedError

# Reuse the mock helpers from the existing restart tests.
from test.k8s_sandbox.pod.test_check_for_pod_restart import _k8s_pod, _make_pod


def _failed_exec_result() -> ExecResult[str]:
    return ExecResult(
        success=False,
        returncode=127,
        stdout="",
        stderr=(
            "timeout: failed to run command "
            "'/var/tmp/.da7be258e003d428/inspect-sandbox-tools': "
            "No such file or directory"
        ),
    )


async def test_race_window_restart_detected_on_recheck():
    pod = _make_pod()

    # Pre-exec check sees stale state; post-exec check sees the restart
    k8s_pods = [
        _k8s_pod(uid="uid-OLD", container_name="default", restart_count=0),
        _k8s_pod(uid="uid-OLD", container_name="default", restart_count=1),
    ]

    with (
        patch("k8s_sandbox._pod.op.k8s_client") as mock_client,
        patch("k8s_sandbox._pod.pod.ExecuteOperation") as mock_exec_cls,
    ):
        mock_client.return_value.read_namespaced_pod.side_effect = k8s_pods
        mock_exec_cls.return_value.exec.return_value = _failed_exec_result()

        with pytest.raises(ContainerRestartedError) as exc_info:
            await pod.exec(
                cmd=["echo", "hello"],
                stdin=None,
                cwd=None,
                env={},
                user=None,
                timeout=None,
            )

    assert exc_info.value.restart_count == 1
    assert exc_info.value.last_reason == "OOMKilled"
    assert pod.info.initial_restart_count == 1


async def test_race_window_pod_replaced_on_recheck():
    pod = _make_pod()

    k8s_pods = [
        _k8s_pod(uid="uid-OLD", container_name="default", restart_count=0),
        _k8s_pod(uid="uid-NEW", container_name="default", restart_count=0),
    ]

    with (
        patch("k8s_sandbox._pod.op.k8s_client") as mock_client,
        patch("k8s_sandbox._pod.pod.ExecuteOperation") as mock_exec_cls,
    ):
        mock_client.return_value.read_namespaced_pod.side_effect = k8s_pods
        mock_exec_cls.return_value.exec.return_value = _failed_exec_result()

        with pytest.raises(PodReplacedError) as exc_info:
            await pod.exec(
                cmd=["echo", "hello"],
                stdin=None,
                cwd=None,
                env={},
                user=None,
                timeout=None,
            )

    assert exc_info.value.old_uid == "uid-OLD"
    assert exc_info.value.new_uid == "uid-NEW"
    assert pod.info.uid == "uid-NEW"


async def test_failed_exec_no_restart_returns_exec_result():
    pod = _make_pod()

    # Both checks see the same state — no restart happened
    k8s_pod = _k8s_pod(uid="uid-OLD", container_name="default", restart_count=0)

    with (
        patch("k8s_sandbox._pod.op.k8s_client") as mock_client,
        patch("k8s_sandbox._pod.pod.ExecuteOperation") as mock_exec_cls,
    ):
        mock_client.return_value.read_namespaced_pod.return_value = k8s_pod
        mock_exec_cls.return_value.exec.return_value = _failed_exec_result()

        result = await pod.exec(
            cmd=["echo", "hello"],
            stdin=None,
            cwd=None,
            env={},
            user=None,
            timeout=None,
        )

    assert not result.success
    assert result.returncode == 127


async def test_warn_mode_still_raises_after_failed_exec():
    # Warn policy means "restart detected but operation hasn't failed yet, just warn."
    # After a failed exec the restart is the cause — always raise.
    pod = _make_pod(restarted_container_behavior="warn")

    k8s_pods = [
        _k8s_pod(uid="uid-OLD", container_name="default", restart_count=0),
        _k8s_pod(uid="uid-OLD", container_name="default", restart_count=1),
    ]

    with (
        patch("k8s_sandbox._pod.op.k8s_client") as mock_client,
        patch("k8s_sandbox._pod.pod.ExecuteOperation") as mock_exec_cls,
    ):
        mock_client.return_value.read_namespaced_pod.side_effect = k8s_pods
        mock_exec_cls.return_value.exec.return_value = _failed_exec_result()

        with pytest.raises(ContainerRestartedError):
            await pod.exec(
                cmd=["echo", "hello"],
                stdin=None,
                cwd=None,
                env={},
                user=None,
                timeout=None,
            )


async def test_warn_mode_api_fast_still_raises_after_failed_exec():
    # API already knows about the restart before exec runs.
    # Pre-exec warn-mode check logs and updates the counter.
    # Post-exec check must still detect it using the pre-exec snapshot.
    pod = _make_pod(restarted_container_behavior="warn")

    k8s_pod = _k8s_pod(uid="uid-OLD", container_name="default", restart_count=1)

    with (
        patch("k8s_sandbox._pod.op.k8s_client") as mock_client,
        patch("k8s_sandbox._pod.pod.ExecuteOperation") as mock_exec_cls,
    ):
        mock_client.return_value.read_namespaced_pod.return_value = k8s_pod
        mock_exec_cls.return_value.exec.return_value = _failed_exec_result()

        with pytest.raises(ContainerRestartedError) as exc_info:
            await pod.exec(
                cmd=["echo", "hello"],
                stdin=None,
                cwd=None,
                env={},
                user=None,
                timeout=None,
            )

    assert exc_info.value.restart_count == 1
    assert pod.info.initial_restart_count == 1


async def test_recheck_api_failure_does_not_mask_exec_result(
    caplog: pytest.LogCaptureFixture,
):
    pod = _make_pod()

    with (
        patch("k8s_sandbox._pod.op.k8s_client") as mock_client,
        patch("k8s_sandbox._pod.pod.ExecuteOperation") as mock_exec_cls,
    ):
        mock_client.return_value.read_namespaced_pod.side_effect = [
            _k8s_pod(uid="uid-OLD", container_name="default", restart_count=0),
            ApiException(status=503, reason="Service Unavailable"),
        ]
        mock_exec_cls.return_value.exec.return_value = _failed_exec_result()

        with caplog.at_level("WARNING"):
            result = await pod.exec(
                cmd=["echo", "hello"],
                stdin=None,
                cwd=None,
                env={},
                user=None,
                timeout=None,
            )

    assert not result.success
    assert result.returncode == 127
    assert any("re-check failed" in r.message for r in caplog.records)

"""Tests for post-exec restart detection (issue #191).

When a container restarts during the race window between
check_for_pod_restart() and the actual exec, the K8s API hasn't yet
updated restart_count. The pre-exec check passes, the exec fails
(binary gone), and the caller gets an opaque failed ExecResult instead
of a typed ContainerRestartedError.

The fix: re-check for restart after a failed exec. If the race window
has closed by then, surface the restart as the root cause.
"""

from unittest.mock import MagicMock, call, patch

import pytest
from inspect_ai.util import ExecResult

from k8s_sandbox._pod.error import ContainerRestartedError, PodReplacedError
from k8s_sandbox._pod.op import PodInfo
from k8s_sandbox._pod.pod import Pod


def _k8s_pod(uid: str, container_name: str, restart_count: int) -> MagicMock:
    """Build a mock K8s pod object returned by read_namespaced_pod."""
    pod = MagicMock()
    pod.metadata.uid = uid
    pod.metadata.name = "agent-env-abc-default-0"
    status = MagicMock()
    status.name = container_name
    status.restart_count = restart_count
    status.last_state.terminated.reason = "OOMKilled"
    pod.status.container_statuses = [status]
    return pod


def _make_pod(
    uid: str = "uid-1",
    restart_count: int = 0,
    behavior: str = "raise",
) -> Pod:
    return Pod(
        name="agent-env-abc-default-0",
        namespace="ns",
        context_name=None,
        default_container_name="default",
        uid=uid,
        initial_restart_count=restart_count,
        restarted_container_behavior=behavior,  # type: ignore[arg-type]
    )


def _failed_exec_result() -> ExecResult[str]:
    """Simulate what happens when inspect-sandbox-tools binary is missing."""
    return ExecResult(
        success=False,
        returncode=127,
        stdout="",
        stderr="timeout: failed to run command '/var/tmp/.da7be258e003d428/inspect-sandbox-tools': No such file or directory",
    )


def _success_exec_result() -> ExecResult[str]:
    return ExecResult(success=True, returncode=0, stdout="ok", stderr="")


# ---------------------------------------------------------------------------
# Reproduction: demonstrate the current bug (race condition)
# ---------------------------------------------------------------------------


class TestRaceConditionBug:
    """These tests document the race condition from issue #191.

    The scenario:
    1. Container restarts (OOM, etc)
    2. Pre-exec check_for_pod_restart() passes (K8s API lag)
    3. Exec fails (binary gone from wiped filesystem)
    4. By the time we could re-check, restart_count has updated

    Before the fix, step 3 returns a failed ExecResult and the caller
    (inspect_ai's exec_remote) gets an opaque error. After the fix,
    step 4 happens and ContainerRestartedError is raised.
    """

    async def test_race_window_restart_detected_on_recheck(self) -> None:
        """The core race: pre-exec check passes, exec fails, post-exec
        check detects the restart that happened during the window."""
        pod = _make_pod(uid="uid-1", restart_count=0)

        # Simulate the race: first API call sees restart_count=0 (stale),
        # second API call sees restart_count=1 (updated).
        k8s_pods = [
            _k8s_pod(uid="uid-1", container_name="default", restart_count=0),
            _k8s_pod(uid="uid-1", container_name="default", restart_count=1),
        ]

        with (
            patch("k8s_sandbox._pod.op.k8s_client") as mock_client,
            patch(
                "k8s_sandbox._pod.pod.ExecuteOperation"
            ) as mock_exec_cls,
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

            err = exc_info.value
            assert err.restart_count == 1
            assert err.last_reason == "OOMKilled"

        # Identity should be refreshed
        assert pod.info.initial_restart_count == 1

    async def test_race_window_pod_replaced_on_recheck(self) -> None:
        """Same race but the pod was replaced entirely (eviction)."""
        pod = _make_pod(uid="uid-OLD", restart_count=0)

        k8s_pods = [
            # Pre-exec: still sees old UID (stale)
            _k8s_pod(uid="uid-OLD", container_name="default", restart_count=0),
            # Post-exec: new UID
            _k8s_pod(uid="uid-NEW", container_name="default", restart_count=0),
        ]

        with (
            patch("k8s_sandbox._pod.op.k8s_client") as mock_client,
            patch(
                "k8s_sandbox._pod.pod.ExecuteOperation"
            ) as mock_exec_cls,
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

            err = exc_info.value
            assert err.old_uid == "uid-OLD"
            assert err.new_uid == "uid-NEW"

        assert pod.info.uid == "uid-NEW"


# ---------------------------------------------------------------------------
# TDD specs: desired behavior after the fix
# ---------------------------------------------------------------------------


class TestPostExecRestartCheck:
    """Specify the post-exec restart check behavior."""

    async def test_failed_exec_no_restart_returns_exec_result(self) -> None:
        """When exec fails but no restart happened, return the ExecResult
        as-is. The caller can handle the failure normally."""
        pod = _make_pod(uid="uid-1", restart_count=0)

        # Both checks see restart_count=0 — no restart
        k8s_pod_stable = _k8s_pod(
            uid="uid-1", container_name="default", restart_count=0
        )

        with (
            patch("k8s_sandbox._pod.op.k8s_client") as mock_client,
            patch(
                "k8s_sandbox._pod.pod.ExecuteOperation"
            ) as mock_exec_cls,
        ):
            mock_client.return_value.read_namespaced_pod.return_value = (
                k8s_pod_stable
            )
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

    async def test_successful_exec_no_post_check(self) -> None:
        """When exec succeeds, don't bother with a post-exec restart check.
        Verify only one API call (the pre-exec check)."""
        pod = _make_pod(uid="uid-1", restart_count=0)

        k8s_pod_stable = _k8s_pod(
            uid="uid-1", container_name="default", restart_count=0
        )

        with (
            patch("k8s_sandbox._pod.op.k8s_client") as mock_client,
            patch(
                "k8s_sandbox._pod.pod.ExecuteOperation"
            ) as mock_exec_cls,
        ):
            mock_client.return_value.read_namespaced_pod.return_value = (
                k8s_pod_stable
            )
            mock_exec_cls.return_value.exec.return_value = _success_exec_result()

            result = await pod.exec(
                cmd=["echo", "hello"],
                stdin=None,
                cwd=None,
                env={},
                user=None,
                timeout=None,
            )

        assert result.success
        # Only one API call: the pre-exec check
        assert mock_client.return_value.read_namespaced_pod.call_count == 1

    async def test_warn_mode_still_raises_after_failed_exec(self) -> None:
        """Even in 'warn' mode, a restart detected after a failed exec
        should raise. The warn policy means 'if you detect a restart but
        the operation hasn't failed yet, just warn.' But if the operation
        DID fail, the restart is the likely cause — surface it."""
        pod = _make_pod(uid="uid-1", restart_count=0, behavior="warn")

        k8s_pods = [
            _k8s_pod(uid="uid-1", container_name="default", restart_count=0),
            _k8s_pod(uid="uid-1", container_name="default", restart_count=1),
        ]

        with (
            patch("k8s_sandbox._pod.op.k8s_client") as mock_client,
            patch(
                "k8s_sandbox._pod.pod.ExecuteOperation"
            ) as mock_exec_cls,
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

    async def test_post_check_api_failure_does_not_mask_exec_result(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """If the post-exec restart check itself fails (e.g. K8s API
        unreachable), don't mask the original exec failure. Return
        the ExecResult and log a warning."""
        pod = _make_pod(uid="uid-1", restart_count=0)

        from kubernetes.client.exceptions import ApiException

        with (
            patch("k8s_sandbox._pod.op.k8s_client") as mock_client,
            patch(
                "k8s_sandbox._pod.pod.ExecuteOperation"
            ) as mock_exec_cls,
        ):
            # Pre-exec check passes, post-exec check fails with API error
            mock_client.return_value.read_namespaced_pod.side_effect = [
                _k8s_pod(uid="uid-1", container_name="default", restart_count=0),
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

    async def test_two_api_calls_on_failed_exec(self) -> None:
        """Verify the post-exec check actually makes a second API call."""
        pod = _make_pod(uid="uid-1", restart_count=0)

        k8s_pods = [
            _k8s_pod(uid="uid-1", container_name="default", restart_count=0),
            _k8s_pod(uid="uid-1", container_name="default", restart_count=1),
        ]

        with (
            patch("k8s_sandbox._pod.op.k8s_client") as mock_client,
            patch(
                "k8s_sandbox._pod.pod.ExecuteOperation"
            ) as mock_exec_cls,
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

        # Two API calls: pre-exec and post-exec
        assert mock_client.return_value.read_namespaced_pod.call_count == 2

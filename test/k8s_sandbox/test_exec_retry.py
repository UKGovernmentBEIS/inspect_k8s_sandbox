from unittest.mock import AsyncMock, MagicMock

import pytest
import websocket
from inspect_ai.util import ExecResult, OutputLimitExceededError
from kubernetes.client.exceptions import ApiException
from tenacity import wait_none

from k8s_sandbox import _sandbox_environment
from k8s_sandbox._pod.error import (
    ExecutableNotFoundError,
    GetReturncodeError,
    PodError,
)
from k8s_sandbox._sandbox_environment import (
    K8sError,
    K8sSandboxEnvironment,
)


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable tenacity's exponential backoff in tests."""
    original = _sandbox_environment._exec_retry

    def _fast_retry():
        r = original()
        r.wait = wait_none()
        return r

    monkeypatch.setattr(_sandbox_environment, "_exec_retry", _fast_retry)


def _make_sandbox_with_mock_pod() -> tuple[K8sSandboxEnvironment, AsyncMock]:
    """Create a K8sSandboxEnvironment with a mocked _pod.exec.

    Returns the sandbox and the mock _pod.exec coroutine for configuring
    side_effect and asserting call_count.
    """
    sandbox = object.__new__(K8sSandboxEnvironment)
    sandbox._pod = MagicMock()
    sandbox._pod.info = MagicMock()
    sandbox._pod.info.name = "test-pod"
    sandbox._config = MagicMock()
    sandbox._config.default_user = None
    sandbox.release = MagicMock()
    sandbox.release.task_name = "test-task"

    sandbox._pod.check_for_pod_restart = AsyncMock()
    mock_exec = AsyncMock()
    sandbox._pod.exec = mock_exec
    return sandbox, mock_exec


def _success_result() -> ExecResult[str]:
    return ExecResult(success=True, returncode=0, stdout="ok", stderr="")


class TestExecRetryTransient:
    """Transient errors should be retried up to 5 times."""

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(
                ApiException(status=503, reason="Service Unavailable"),
                id="ApiException",
            ),
            pytest.param(
                websocket.WebSocketConnectionClosedException("gone"),
                id="WebSocketException",
            ),
            pytest.param(
                ConnectionError("connection refused"),
                id="ConnectionError",
            ),
            pytest.param(
                OSError("socket error"),
                id="OSError",
            ),
            pytest.param(
                PodError("WebSocket connection lost", pod="test-pod"),
                id="PodError",
            ),
            pytest.param(
                GetReturncodeError("no return code"),
                id="GetReturncodeError",
            ),
        ],
    )
    async def test_transient_error_is_retried(self, error: Exception) -> None:
        sandbox, mock_exec = _make_sandbox_with_mock_pod()
        mock_exec.side_effect = [error, _success_result()]

        result = await sandbox.exec(["echo", "hello"])

        assert result.success
        assert result.stdout == "ok"
        assert mock_exec.call_count == 2

    async def test_transient_error_retried_multiple_times(self) -> None:
        sandbox, mock_exec = _make_sandbox_with_mock_pod()
        mock_exec.side_effect = [
            ApiException(status=503, reason="Service Unavailable"),
            ApiException(status=503, reason="Service Unavailable"),
            _success_result(),
        ]

        result = await sandbox.exec(["echo", "hello"])

        assert result.success
        assert mock_exec.call_count == 3


class TestExecRetryPermanent:
    """Permanent errors should NOT be retried."""

    @pytest.mark.parametrize(
        "error, expected_exception",
        [
            pytest.param(
                ExecutableNotFoundError("/bin/sh not found"),
                K8sError,
                id="ExecutableNotFoundError",
            ),
            pytest.param(
                RuntimeError("Pod UID mismatch"),
                K8sError,
                id="RuntimeError",
            ),
            pytest.param(
                PermissionError("permission denied"),
                PermissionError,
                id="PermissionError",
            ),
        ],
    )
    async def test_permanent_error_is_not_retried(
        self, error: Exception, expected_exception: type[Exception]
    ) -> None:
        sandbox, mock_exec = _make_sandbox_with_mock_pod()
        mock_exec.side_effect = error

        with pytest.raises(expected_exception):
            await sandbox.exec(["echo", "hello"])

        assert mock_exec.call_count == 1


class TestExecRetryExpectedExceptions:
    """Expected exceptions should pass through without retry."""

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(TimeoutError("timed out"), id="TimeoutError"),
            pytest.param(
                UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
                id="UnicodeDecodeError",
            ),
            pytest.param(
                OutputLimitExceededError(limit_str="10MB", truncated_output="..."),
                id="OutputLimitExceededError",
            ),
        ],
    )
    async def test_expected_exception_passes_through(self, error: Exception) -> None:
        sandbox, mock_exec = _make_sandbox_with_mock_pod()
        mock_exec.side_effect = error

        with pytest.raises(type(error)):
            await sandbox.exec(["echo", "hello"])

        assert mock_exec.call_count == 1


class TestExecRetryExhausted:
    """After 5 failed attempts, the error should propagate."""

    async def test_retries_exhausted_raises_k8s_error(self) -> None:
        sandbox, mock_exec = _make_sandbox_with_mock_pod()
        mock_exec.side_effect = ApiException(status=503, reason="Service Unavailable")

        with pytest.raises(K8sError):
            await sandbox.exec(["echo", "hello"])

        assert mock_exec.call_count == 5

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import websocket
from inspect_ai.util import ExecResult, OutputLimitExceededError
from kubernetes.client.exceptions import ApiException
from tenacity import wait_none

from k8s_sandbox._pod.error import (
    ExecutableNotFoundError,
    GetReturncodeError,
    PodError,
)
from k8s_sandbox._sandbox_environment import (
    K8sError,
    K8sSandboxEnvironment,
    _exec_retry,
)


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable tenacity's exponential backoff in tests."""
    monkeypatch.setattr(_exec_retry, "wait", wait_none())


def _make_sandbox() -> K8sSandboxEnvironment:
    """Create a K8sSandboxEnvironment with mocked internals."""
    sandbox = object.__new__(K8sSandboxEnvironment)
    sandbox._pod = MagicMock()
    sandbox._pod.info = MagicMock()
    sandbox._pod.info.name = "test-pod"
    sandbox._config = MagicMock()
    sandbox._config.default_user = None
    sandbox.release = MagicMock()
    sandbox.release.task_name = "test-task"

    sandbox._pod.check_for_pod_restart = AsyncMock()
    sandbox._pod.exec = AsyncMock()
    sandbox._pod.read_file = AsyncMock()
    sandbox._pod.write_file = AsyncMock()
    return sandbox


def _make_sandbox_with_mock_pod() -> tuple[K8sSandboxEnvironment, AsyncMock]:
    """Create a K8sSandboxEnvironment with a mocked _pod.exec.

    Returns the sandbox and the mock _pod.exec coroutine for configuring
    side_effect and asserting call_count.
    """
    sandbox = _make_sandbox()
    return sandbox, sandbox._pod.exec


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


# -- read_file retry tests --


def _read_file_side_effect(error: Exception, content: bytes = b"file-content"):
    """Return a side_effect that fails once then writes content to dst."""
    calls = 0

    async def impl(src: Path, dst):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error
        dst.write(content)

    return impl


class TestReadFileRetryTransient:
    """Transient errors during read_file should be retried."""

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
                PodError("WebSocket connection lost", pod="test-pod"),
                id="PodError",
            ),
        ],
    )
    async def test_transient_error_is_retried(self, error: Exception) -> None:
        sandbox = _make_sandbox()
        mock_read = sandbox._pod.read_file
        mock_read.side_effect = _read_file_side_effect(error)

        contents = await sandbox.read_file("/tmp/test.txt")

        assert contents == "file-content"
        assert mock_read.call_count == 2


class TestReadFileRetryPermanent:
    """Permanent errors during read_file should NOT be retried."""

    async def test_permission_error_not_retried(self) -> None:
        sandbox = _make_sandbox()
        mock_read = sandbox._pod.read_file
        mock_read.side_effect = PermissionError("permission denied")

        with pytest.raises(PermissionError):
            await sandbox.read_file("/tmp/test.txt")

        assert mock_read.call_count == 1

    async def test_file_not_found_not_retried(self) -> None:
        sandbox = _make_sandbox()
        mock_read = sandbox._pod.read_file
        mock_read.side_effect = FileNotFoundError("no such file")

        with pytest.raises(FileNotFoundError):
            await sandbox.read_file("/tmp/test.txt")

        assert mock_read.call_count == 1


class TestReadFileRetryExhausted:
    """After 5 failed attempts, read_file should propagate the error."""

    async def test_retries_exhausted_raises_k8s_error(self) -> None:
        sandbox = _make_sandbox()
        mock_read = sandbox._pod.read_file
        mock_read.side_effect = ApiException(status=503, reason="Service Unavailable")

        with pytest.raises(K8sError):
            await sandbox.read_file("/tmp/test.txt")

        assert mock_read.call_count == 5


# -- write_file retry tests --


class TestWriteFileRetryTransient:
    """Transient errors during write_file should be retried."""

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
                PodError("WebSocket connection lost", pod="test-pod"),
                id="PodError",
            ),
        ],
    )
    async def test_transient_error_is_retried(self, error: Exception) -> None:
        sandbox = _make_sandbox()
        mock_write = sandbox._pod.write_file
        mock_write.side_effect = [error, None]

        await sandbox.write_file("/tmp/test.txt", "hello")

        assert mock_write.call_count == 2


class TestWriteFileRetryPermanent:
    """Permanent errors during write_file should NOT be retried."""

    async def test_permission_error_not_retried(self) -> None:
        sandbox = _make_sandbox()
        mock_write = sandbox._pod.write_file
        mock_write.side_effect = PermissionError("permission denied")

        with pytest.raises(PermissionError):
            await sandbox.write_file("/tmp/test.txt", "hello")

        assert mock_write.call_count == 1

    async def test_is_a_directory_not_retried(self) -> None:
        sandbox = _make_sandbox()
        mock_write = sandbox._pod.write_file
        mock_write.side_effect = IsADirectoryError("is a directory")

        with pytest.raises(IsADirectoryError):
            await sandbox.write_file("/tmp/test.txt", "hello")

        assert mock_write.call_count == 1


class TestWriteFileRetryExhausted:
    """After 5 failed attempts, write_file should propagate the error."""

    async def test_retries_exhausted_raises_k8s_error(self) -> None:
        sandbox = _make_sandbox()
        mock_write = sandbox._pod.write_file
        mock_write.side_effect = ApiException(status=503, reason="Service Unavailable")

        with pytest.raises(K8sError):
            await sandbox.write_file("/tmp/test.txt", "hello")

        assert mock_write.call_count == 5

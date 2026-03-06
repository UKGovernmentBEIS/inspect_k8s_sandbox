from unittest.mock import MagicMock

import pytest
from kubernetes.stream.ws_client import WSClient  # type: ignore

from k8s_sandbox._pod.error import GetReturncodeError
from k8s_sandbox._pod.execute import ExecuteOperation


def _make_ws_client(
    stdout_frames: list[bytes],
    stderr_frames: list[bytes] | None = None,
    error_after_close: Exception | None = None,
) -> WSClient:
    """Create a mock WSClient that delivers frames then optionally raises after close.

    Simulates the real WSClient behavior where, after the sentinel triggers close(),
    the next call to ws_client.update() (called internally by peek_stdout/peek_stderr)
    raises BrokenPipeError or ConnectionResetError because the socket is gone.

    In the real code, peek_stdout()/peek_stderr() call update(timeout=0) internally,
    which can raise these errors. We simulate this by having update() raise after
    close() has been called.
    """
    ws = MagicMock(spec=WSClient)

    stdout_queue = list(stdout_frames)
    stderr_queue = list(stderr_frames or [])
    current_stdout: bytes | None = None
    current_stderr: bytes | None = None
    closed = False

    def close_ws(**kwargs: object) -> None:
        nonlocal closed
        closed = True

    ws.close.side_effect = close_ws

    # After close(), is_open still returns True for one more loop iteration.
    # This is realistic: the while loop checks is_open(), which may briefly still
    # return True before the socket state propagates. The error comes from update().
    is_open_calls_after_close = 0

    def is_open() -> bool:
        nonlocal is_open_calls_after_close
        if closed:
            is_open_calls_after_close += 1
            # Return True once after close so the loop re-enters
            return is_open_calls_after_close <= 1
        return True

    ws.is_open.side_effect = is_open

    def update(timeout: float | None = None) -> None:
        nonlocal current_stdout, current_stderr
        if closed and error_after_close is not None:
            raise error_after_close
        if closed:
            return
        current_stderr = stderr_queue.pop(0) if stderr_queue else None
        current_stdout = stdout_queue.pop(0) if stdout_queue else None

    ws.update.side_effect = update

    def peek_stderr(**kwargs: object) -> bytes:
        return current_stderr or b""

    def read_stderr(**kwargs: object) -> bytes:
        nonlocal current_stderr
        data = current_stderr or b""
        current_stderr = None
        return data

    ws.peek_stderr.side_effect = peek_stderr
    ws.read_stderr.side_effect = read_stderr

    def peek_stdout(**kwargs: object) -> bytes:
        return current_stdout or b""

    def read_stdout(**kwargs: object) -> bytes:
        nonlocal current_stdout
        data = current_stdout or b""
        current_stdout = None
        return data

    ws.peek_stdout.side_effect = peek_stdout
    ws.read_stdout.side_effect = read_stdout

    return ws


class TestBrokenPipeAfterSentinel:
    """Tests for BrokenPipeError/ConnectionResetError after the sentinel is received.

    After the sentinel is processed, ws_client.close() is called. On the next loop
    iteration, update() (called directly or from peek_*) raises BrokenPipeError or
    ConnectionResetError because the underlying socket is closed. Currently these
    propagate uncaught — after the fix, they should be caught and the already-captured
    result should be returned.
    """

    def test_broken_pipe_after_sentinel_with_timeout_raises_timeout_error(self) -> None:
        """Sentinel rc=124 with timeout: raise TimeoutError."""
        ws = _make_ws_client(
            stdout_frames=[b"output<completed-sentinel-value-124>"],
            error_after_close=BrokenPipeError("socket closed"),
        )

        executor = ExecuteOperation(MagicMock())
        with pytest.raises(TimeoutError):
            executor._handle_shell_output(ws, user=None, timeout=30)

    def test_broken_pipe_after_sentinel_without_timeout_returns_result(self) -> None:
        """Sentinel rc=0, no timeout: return ExecResult."""
        ws = _make_ws_client(
            stdout_frames=[b"hello<completed-sentinel-value-0>"],
            error_after_close=BrokenPipeError("socket closed"),
        )

        executor = ExecuteOperation(MagicMock())
        result = executor._handle_shell_output(ws, user=None, timeout=None)
        assert result.returncode == 0
        assert result.stdout == "hello"
        assert result.stderr == ""

    def test_connection_reset_after_sentinel_returns_result(self) -> None:
        """Same as above but with ConnectionResetError."""
        ws = _make_ws_client(
            stdout_frames=[b"hello<completed-sentinel-value-0>"],
            error_after_close=ConnectionResetError("connection reset"),
        )

        executor = ExecuteOperation(MagicMock())
        result = executor._handle_shell_output(ws, user=None, timeout=None)
        assert result.returncode == 0
        assert result.stdout == "hello"
        assert result.stderr == ""


class TestBrokenPipeWithoutSentinel:
    """Tests for BrokenPipeError before the sentinel is received."""

    def _make_dead_ws(
        self,
        error: Exception,
    ) -> MagicMock:
        ws = MagicMock(spec=WSClient)
        # is_open: True (enters loop), False (for get_returncode)
        ws.is_open.side_effect = [True, False]
        ws.peek_stderr.return_value = b""
        ws.update.side_effect = error
        ws.read_channel.return_value = ""
        return ws

    def test_broken_pipe_without_sentinel_raises(self) -> None:
        """BrokenPipe before sentinel → break → get_returncode fails."""
        ws = self._make_dead_ws(BrokenPipeError("socket closed"))

        executor = ExecuteOperation(MagicMock())
        with pytest.raises(GetReturncodeError):
            executor._handle_shell_output(ws, user=None, timeout=None)

    def test_connection_reset_without_sentinel_raises(self) -> None:
        """ConnectionReset before sentinel → same behavior."""
        ws = self._make_dead_ws(
            ConnectionResetError("connection reset"),
        )

        executor = ExecuteOperation(MagicMock())
        with pytest.raises(GetReturncodeError):
            executor._handle_shell_output(ws, user=None, timeout=None)

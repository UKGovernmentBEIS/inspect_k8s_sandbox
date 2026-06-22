from contextlib import contextmanager
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from inspect_ai.util import ExecResult
from kubernetes.stream.ws_client import WSClient  # type: ignore

import k8s_sandbox._pod.op as op_module
from k8s_sandbox._pod.error import PodError
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
        ws.is_open.return_value = True
        ws.update.side_effect = error
        return ws

    def test_broken_pipe_without_sentinel_raises(self) -> None:
        """BrokenPipe before sentinel → PodError."""
        ws = self._make_dead_ws(BrokenPipeError("socket closed"))

        executor = ExecuteOperation(MagicMock())
        with pytest.raises(PodError, match="WebSocket connection lost"):
            executor._handle_shell_output(ws, user=None, timeout=None)


class TestNonUtf8Output:
    """A command emitting non-UTF-8 bytes must not abort exec() (issue #206)."""

    def test_binary_byte_with_sentinel_in_same_frame(self) -> None:
        ws = _make_ws_client(
            stdout_frames=[b"before \xbb after<completed-sentinel-value-0>"],
        )

        executor = ExecuteOperation(MagicMock())
        result = executor._handle_shell_output(ws, user=None, timeout=None)

        assert result.returncode == 0
        assert result.stdout == "before � after"

    def test_binary_byte_on_stderr(self) -> None:
        ws = _make_ws_client(
            stdout_frames=[b"<completed-sentinel-value-0>"],
            stderr_frames=[b"warn \xbb done\n"],
        )

        executor = ExecuteOperation(MagicMock())
        result = executor._handle_shell_output(ws, user=None, timeout=None)

        assert result.returncode == 0
        assert result.stderr == "warn � done\n"

    def test_binary_byte_in_earlier_frame_than_sentinel(self) -> None:
        ws = _make_ws_client(
            stdout_frames=[b"raw \xbb bytes", b"more<completed-sentinel-value-0>"],
        )

        executor = ExecuteOperation(MagicMock())
        result = executor._handle_shell_output(ws, user=None, timeout=None)

        assert result.returncode == 0
        assert result.stdout == "raw � bytesmore"


class TestRunuserErrors:
    def test_user_command_runuser_error_with_sentinel_returns_result(self) -> None:
        ws = _make_ws_client(
            stdout_frames=[b"<completed-sentinel-value-1>"],
            stderr_frames=[b"runuser: user foo does not exist\n"],
        )

        executor = ExecuteOperation(MagicMock())
        result = executor._handle_shell_output(ws, user="nobody", timeout=None)

        assert result.returncode == 1
        assert result.stderr == "runuser: user foo does not exist\n"

    def test_wrapper_runuser_error_without_sentinel_raises(self) -> None:
        ws = MagicMock(spec=WSClient)
        current_stderr: bytes | None = None

        ws.is_open.side_effect = [True, False]
        ws.close.return_value = None

        def update(timeout: float | None = None) -> None:
            nonlocal current_stderr
            current_stderr = b"runuser: user foo does not exist\n"

        def peek_stderr(**kwargs: object) -> bytes:
            return current_stderr or b""

        def read_stderr(**kwargs: object) -> bytes:
            nonlocal current_stderr
            data = current_stderr or b""
            current_stderr = None
            return data

        ws.update.side_effect = update
        ws.peek_stderr.side_effect = peek_stderr
        ws.read_stderr.side_effect = read_stderr
        ws.peek_stdout.return_value = b""

        with patch("k8s_sandbox._pod.execute.get_returncode", return_value=1):
            executor = ExecuteOperation(MagicMock())
            with pytest.raises(RuntimeError, match="does not appear to exist"):
                executor._handle_shell_output(ws, user="foo", timeout=None)

    def test_non_runuser_error_without_sentinel_returns_result(self) -> None:
        ws = MagicMock(spec=WSClient)
        ws.is_open.return_value = False

        executor = ExecuteOperation(MagicMock())
        with patch("k8s_sandbox._pod.execute.get_returncode", return_value=1):
            result = executor._handle_shell_output(ws, user="foo", timeout=None)

        assert result.returncode == 1


class TestConnectionResetWithoutSentinel:
    def test_connection_reset_without_sentinel_raises(self) -> None:
        """ConnectionReset before sentinel → PodError."""
        ws = MagicMock(spec=WSClient)
        ws.is_open.return_value = True
        ws.update.side_effect = ConnectionResetError("connection reset")

        executor = ExecuteOperation(MagicMock())
        with pytest.raises(PodError, match="WebSocket connection lost"):
            executor._handle_shell_output(ws, user=None, timeout=None)


class TestExecChunksStdin:
    def test_exec_writes_shell_script_in_chunks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Tiny chunk size (patched in op.py, where _write_stdin_chunked reads it)
        # plus stubbed shell creation + output handling, so only the write path runs.
        monkeypatch.setattr(op_module, "_STDIN_CHUNK_SIZE", 16)
        ws = MagicMock(spec=WSClient)

        @contextmanager
        def fake_interactive_shell(
            user: str | None,
        ) -> Generator[MagicMock, None, None]:
            yield ws

        op = ExecuteOperation(MagicMock())
        monkeypatch.setattr(op, "_interactive_shell", fake_interactive_shell)
        sentinel = ExecResult(success=True, returncode=0, stdout="", stderr="")
        monkeypatch.setattr(op, "_handle_shell_output", lambda *a, **k: sentinel)

        # The assembled script (base64 input + pipeline) is several hundred bytes,
        # well above the 16-byte chunk size, so it splits into many frames.
        result = op.exec(
            ["cat"], stdin=b"x" * 100, cwd=None, env={}, user=None, timeout=None
        )

        expected_script = op._build_shell_script(["cat"], b"x" * 100, None, {}, None)
        written = "".join(call.args[0] for call in ws.write_stdin.call_args_list)
        assert written == expected_script
        assert ws.write_stdin.call_count > 1
        assert all(len(c.args[0]) <= 16 for c in ws.write_stdin.call_args_list)
        assert result is sentinel

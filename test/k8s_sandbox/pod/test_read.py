import io
import threading
import time
from typing import Callable
from unittest.mock import MagicMock

import pytest
from kubernetes.stream.ws_client import WSClient  # type: ignore

import k8s_sandbox._pod.op as op_module
import k8s_sandbox._pod.read as read_module
from k8s_sandbox._pod.error import PodError
from k8s_sandbox._pod.read import ReadFileOperation


def _finish_within(seconds: float, fn: Callable[[], object]) -> object:
    """Run fn on a worker thread; fail if it is still waiting after `seconds`."""
    outcome: list[object] = []

    def run() -> None:
        try:
            outcome.append(fn())
        except BaseException as e:  # noqa: BLE001 - the test inspects the exception
            outcome.append(e)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        pytest.fail(f"still waiting on a pod that will never answer after {seconds}s")
    return outcome[0]


class TestPodStopsAnswering:
    """`head` on a live pod streams continuously; a long silence means it is gone."""

    def test_read_file_raises_when_the_stream_stalls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(read_module, "READ_STALL_SECONDS", 0.2)
        monkeypatch.setattr(op_module, "TRANSPORT_POLL_SECONDS", 0.05)
        ws = MagicMock(spec=WSClient)
        ws.is_open.return_value = True

        def update(timeout: float | None = None) -> None:
            time.sleep(min(timeout if timeout is not None else 60.0, 60.0))

        ws.update.side_effect = update
        ws.peek_stdout.return_value = False
        ws.peek_stderr.return_value = False
        reader = ReadFileOperation(MagicMock())

        outcome = _finish_within(
            5.0, lambda: reader._handle_stream_output(ws, io.BytesIO())
        )

        assert isinstance(outcome, PodError)
        assert "stalled" in str(outcome)

    def test_read_file_tolerates_a_slow_stream_that_keeps_delivering(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bytes keep arriving, each gap shorter than the stall bound: no error."""
        monkeypatch.setattr(read_module, "READ_STALL_SECONDS", 0.4)
        monkeypatch.setattr(op_module, "TRANSPORT_POLL_SECONDS", 0.05)
        monkeypatch.setattr(read_module, "get_returncode", lambda ws: 0)
        ws = MagicMock(spec=WSClient)
        frames = [b"a", b"b", b"c", b"d", b"e"]
        delivered = 0
        ws.is_open.side_effect = lambda: delivered < len(frames)

        def update(timeout: float | None = None) -> None:
            # Slower than the poll interval, faster than the stall bound; the total
            # transfer (0.75s) is longer than the stall bound, so only per-byte
            # progress can keep this read alive.
            time.sleep(0.15)

        def read_stdout() -> bytes:
            nonlocal delivered
            frame = frames[delivered]
            delivered += 1
            return frame

        ws.update.side_effect = update
        ws.peek_stdout.side_effect = lambda: delivered < len(frames)
        ws.read_stdout.side_effect = read_stdout
        ws.peek_stderr.return_value = False
        dst = io.BytesIO()

        ReadFileOperation(MagicMock())._handle_stream_output(ws, dst)

        assert dst.getvalue() == b"abcde"

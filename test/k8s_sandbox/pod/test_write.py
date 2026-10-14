import json
import socket
import struct
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Generator
from unittest.mock import MagicMock, patch

import pytest
from kubernetes.stream.ws_client import WSClient  # type: ignore[import-untyped]
from websocket import ABNF, WebSocket

import k8s_sandbox._pod.op as op_module
import k8s_sandbox._pod.write as write_module
from k8s_sandbox._pod.error import PodError
from k8s_sandbox._pod.execute import ExecuteOperation
from k8s_sandbox._pod.write import WriteFileOperation


def test_write_file_writes_data_via_chunked_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(op_module, "_STDIN_CHUNK_SIZE", 4)
    op = WriteFileOperation(MagicMock())
    ws = MagicMock(spec=WSClient)
    captured: dict[str, int] = {}

    @contextmanager
    def fake_start_write_command(
        dst: Path, file_size: int
    ) -> Generator[MagicMock, None, None]:
        captured["file_size"] = file_size
        yield ws

    monkeypatch.setattr(op, "_start_write_command", fake_start_write_command)
    monkeypatch.setattr(op, "_handle_stream_output", lambda ws_client: None)
    data = b"abcdefghij"

    op.write_file(data, Path("/tmp/dst"))

    assert captured["file_size"] == len(data)
    assert b"".join(call.args[0] for call in ws.write_stdin.call_args_list) == data
    assert ws.write_stdin.call_count > 1


class _FakePod:
    """The pod's end of an operation's connection, over a local socket pair.

    The operation gets a real `WSClient`, so it sends and waits on a real socket.
    """

    def __init__(self) -> None:
        ours, self._end = socket.socketpair()
        client_socket = WebSocket()
        client_socket.sock = ours
        client_socket.connected = True
        with patch(
            "kubernetes.stream.ws_client.create_websocket", return_value=client_socket
        ):
            self.transport = WSClient(MagicMock(), "wss://pod", None, capture_all=False)
        self._frames = WebSocket()
        self._frames.sock = self._end
        self._frames.connected = True

    def read_stdin(self, size: int) -> None:
        """Read stdin frames until `size` bytes of input have arrived."""
        received = 0
        while received < size:
            frame = self._frames.recv_frame()
            if frame.data[:1] == b"\x00":
                received += len(frame.data) - 1

    def report_success(self) -> None:
        """Report success on the status channel and close, as the API server does."""
        status = b"\x03" + json.dumps({"status": "Success"}).encode()
        for opcode, payload in [
            (ABNF.OPCODE_BINARY, status),
            (ABNF.OPCODE_CLOSE, struct.pack("!H", 1000)),
        ]:
            frame = ABNF(fin=1, opcode=opcode, mask_value=0, data=payload)
            self._end.sendall(frame.format())

    def close(self) -> None:
        self._end.close()


@pytest.fixture
def fake_pod() -> Generator[_FakePod, None, None]:
    pod = _FakePod()
    try:
        with (
            patch("k8s_sandbox._pod.op.k8s_client"),
            patch("k8s_sandbox._pod.op.stream", return_value=pod.transport),
        ):
            yield pod
    finally:
        # Releases a worker still blocked on the pod, whatever the outcome.
        pod.close()


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


def _write_file(data: bytes) -> Callable[[], object]:
    return lambda: WriteFileOperation(MagicMock()).write_file(data, Path("/dst"))


def _exec_with_input(data: bytes) -> Callable[[], object]:
    return lambda: ExecuteOperation(MagicMock()).exec(["cat"], data, None, {}, None, 10)


class TestPodStopsAnswering:
    def test_write_file_completes_when_the_pod_reads_it_and_reports_success(
        self, fake_pod: _FakePod
    ) -> None:
        data = b"x" * (3 * op_module._STDIN_CHUNK_SIZE + 5)

        def pod() -> None:
            fake_pod.read_stdin(len(data))
            fake_pod.report_success()

        threading.Thread(target=pod, daemon=True).start()

        assert _finish_within(10.0, _write_file(data)) is None

    @pytest.mark.parametrize(
        "operation", [_write_file, _exec_with_input], ids=["write_file", "exec"]
    )
    def test_sending_to_a_pod_that_stops_reading_fails(
        self,
        fake_pod: _FakePod,
        monkeypatch: pytest.MonkeyPatch,
        operation: Callable[[bytes], Callable[[], object]],
    ) -> None:
        """Both send the pod their input on stdin; a pod that never reads it is gone."""
        monkeypatch.setattr(op_module, "TRANSPORT_STALL_SECONDS", 0.5)

        outcome = _finish_within(10.0, operation(b"x" * 32 * 1024**2))

        assert isinstance(outcome, PodError)
        assert "stalled" in str(outcome)

    def test_write_file_fails_when_the_pod_never_reports_completion(
        self, fake_pod: _FakePod, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pod takes the whole file but never says the write finished."""
        monkeypatch.setattr(write_module, "WRITE_COMPLETION_SECONDS", 0.5)
        monkeypatch.setattr(op_module, "TRANSPORT_POLL_SECONDS", 0.05)
        data = b"x" * 1024
        threading.Thread(
            target=fake_pod.read_stdin, args=(len(data),), daemon=True
        ).start()

        outcome = _finish_within(10.0, _write_file(data))

        assert isinstance(outcome, PodError)
        assert "did not finish" in str(outcome)

import io
from pathlib import Path
from typing import Any, Generator
from unittest.mock import MagicMock

import pytest
from kubernetes.stream.ws_client import WSClient  # type: ignore[import-untyped]

from k8s_sandbox._pod.error import PodError
from k8s_sandbox._pod.read import COMPLETE_SENTINEL, ReadFileOperation
from test.k8s_sandbox.pod.ws_frames import failure_status, make_ws_client


def test_read_file_opens_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    # A read exec without stdin hangs or truncates on Kata Containers' Go shim
    # (kata-containers/kata-containers#9071).
    op = ReadFileOperation(MagicMock())
    exec_kwargs: dict[str, Any] = {}

    def fake_exec(**kwargs: Any) -> Generator[MagicMock, None, None]:
        exec_kwargs.update(kwargs)
        yield MagicMock(spec=WSClient)

    monkeypatch.setattr(op, "create_websocket_client_for_exec", fake_exec)
    monkeypatch.setattr(op, "_handle_stream_output", lambda ws_client, dst: None)

    op.read_file(Path("/tmp/src"), io.BytesIO())

    assert exec_kwargs["stdin"] is True
    assert exec_kwargs["command"][-1] == "/tmp/src"


def test_read_file_command_is_all_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    # kubernetes>=37 validates each element and rejects the call before sending it.
    op = ReadFileOperation(MagicMock())
    exec_kwargs: dict[str, Any] = {}

    def fake_exec(**kwargs: Any) -> Generator[MagicMock, None, None]:
        exec_kwargs.update(kwargs)
        yield MagicMock(spec=WSClient)

    monkeypatch.setattr(op, "create_websocket_client_for_exec", fake_exec)
    monkeypatch.setattr(op, "_handle_stream_output", lambda ws_client, dst: None)

    op.read_file(Path("/tmp/src"), io.BytesIO())

    command = exec_kwargs["command"]
    assert all(isinstance(arg, str) for arg in command), command


def test_read_file_writes_every_frame_of_the_file() -> None:
    # A large file arrives as thousands of frames; dropping one of the middle
    # frames produces a short file which still ends with the sentinel.
    chunks = [b"chunk-%d;" % i for i in range(1000)]
    ws = make_ws_client(stdout_frames=chunks + [COMPLETE_SENTINEL])
    dst = io.BytesIO()
    op = ReadFileOperation(MagicMock())

    op._handle_stream_output(ws, dst)

    assert dst.getvalue() == b"".join(chunks)


def test_read_file_strips_a_sentinel_split_across_frames() -> None:
    ws = make_ws_client(
        stdout_frames=[b"file contents" + COMPLETE_SENTINEL[:4], COMPLETE_SENTINEL[4:]]
    )
    dst = io.BytesIO()
    op = ReadFileOperation(MagicMock())

    op._handle_stream_output(ws, dst)

    assert dst.getvalue() == b"file contents"


def test_read_file_raises_if_the_sentinel_never_arrives() -> None:
    # A client which reads more slowly than the pod writes loses the tail of the
    # output, and the status channel reports success regardless
    # (kubernetes/kubernetes#142376).
    ws = make_ws_client(stdout_frames=[b"the first half of the file"])
    op = ReadFileOperation(MagicMock())

    with pytest.raises(PodError, match="incomplete"):
        op._handle_stream_output(ws, io.BytesIO())


def test_read_file_reads_an_empty_file() -> None:
    ws = make_ws_client(stdout_frames=[COMPLETE_SENTINEL])
    dst = io.BytesIO()
    op = ReadFileOperation(MagicMock())

    op._handle_stream_output(ws, dst)

    assert dst.getvalue() == b""


def test_read_file_raises_for_a_missing_file() -> None:
    ws = make_ws_client(
        stderr_frames=[b"head: /nope: No such file or directory\n"],
        status=failure_status(1),
    )
    op = ReadFileOperation(MagicMock())

    with pytest.raises(FileNotFoundError, match="No such file or directory"):
        op._handle_stream_output(ws, io.BytesIO())

import io
from pathlib import Path
from typing import Any, Generator
from unittest.mock import MagicMock

import pytest
from kubernetes.stream.ws_client import WSClient  # type: ignore[import-untyped]

from k8s_sandbox._pod.read import ReadFileOperation


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

import io
from pathlib import Path
from typing import Any, Generator
from unittest.mock import MagicMock

import pytest
from inspect_ai.util import SandboxUserUnsupportedError
from kubernetes.stream.ws_client import WSClient  # type: ignore

from k8s_sandbox._pod.read import ReadFileOperation
from k8s_sandbox._pod.user import raise_for_unsupported_user, run_as
from k8s_sandbox._pod.write import WriteFileOperation


class TestRunAs:
    def test_no_user_runs_the_command_unwrapped(self) -> None:
        assert run_as(None, ["head", "-c", "1", "/f"]) == ["head", "-c", "1", "/f"]

    def test_user_runs_the_command_through_runuser(self) -> None:
        command = run_as("agent", ["head", "-c", "1", "/a b"])

        assert command[:2] == ["/bin/sh", "-c"]
        assert "exec runuser -u agent -- head -c 1 '/a b'" in command[2]
        assert "then exec head -c 1 '/a b'; fi" in command[2]

    def test_an_empty_user_is_left_to_runuser(self) -> None:
        assert run_as("", ["head"]) == ["runuser", "-u", "", "--", "head"]


class TestRaiseForUnsupportedUser:
    @pytest.mark.parametrize(
        "stderr",
        [
            "runuser: user agent does not exist or the user entry does not contain "
            "all the required fields",
            "runuser: may not be used by non-root users",
            "runuser: cannot set groups: Operation not permitted",
            "/bin/sh: 1: exec: runuser: not found",
        ],
    )
    def test_runuser_failures_raise(self, stderr: str) -> None:
        with pytest.raises(SandboxUserUnsupportedError, match="'agent'"):
            raise_for_unsupported_user(stderr, "agent")

    @pytest.mark.parametrize(
        "stderr",
        [
            "head: /f: Permission denied",
            "newgrp: cannot set groups: Operation not permitted",
        ],
    )
    def test_other_failures_are_left_alone(self, stderr: str) -> None:
        raise_for_unsupported_user(stderr, "agent")


def _capture_command(
    monkeypatch: pytest.MonkeyPatch, op: ReadFileOperation | WriteFileOperation
) -> dict[str, Any]:
    exec_kwargs: dict[str, Any] = {}

    def fake_exec(**kwargs: Any) -> Generator[MagicMock, None, None]:
        exec_kwargs.update(kwargs)
        yield MagicMock(spec=WSClient)

    monkeypatch.setattr(op, "create_websocket_client_for_exec", fake_exec)
    monkeypatch.setattr(op, "_handle_stream_output", lambda *args: None)
    monkeypatch.setattr(op, "_write_stdin_chunked", lambda *args: None)
    return exec_kwargs


def test_read_file_runs_as_the_user(monkeypatch: pytest.MonkeyPatch) -> None:
    op = ReadFileOperation(MagicMock())
    exec_kwargs = _capture_command(monkeypatch, op)

    op.read_file(Path("/tmp/src"), io.BytesIO(), "agent")

    assert "exec runuser -u agent -- head -c" in exec_kwargs["command"][2]


def test_write_file_runs_as_the_user(monkeypatch: pytest.MonkeyPatch) -> None:
    op = WriteFileOperation(MagicMock())
    exec_kwargs = _capture_command(monkeypatch, op)

    op.write_file(b"data", Path("/tmp/dst"), "agent")

    assert "exec runuser -u agent -- /bin/sh -c" in exec_kwargs["command"][2]

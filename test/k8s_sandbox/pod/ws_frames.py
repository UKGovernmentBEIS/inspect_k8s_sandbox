"""Helpers for faking an exec websocket at the frame level.

The pod operations demultiplex the exec stream's frames themselves (see
`k8s_sandbox._pod.stream`), so the fakes here script what the socket delivers
rather than what `WSClient` would have buffered.
"""

from types import SimpleNamespace
from typing import Any, Sequence
from unittest.mock import MagicMock

from kubernetes.stream.ws_client import (  # type: ignore[import-untyped]
    ERROR_CHANNEL,
    STDERR_CHANNEL,
    STDOUT_CHANNEL,
    WSClient,
)
from websocket import ABNF  # type: ignore[import-untyped]

SUCCESS_STATUS = b'{"metadata":{},"status":"Success"}'


def failure_status(returncode: int) -> bytes:
    """The status channel's payload for a command which exited non-zero."""
    return (
        b'{"metadata":{},"status":"Failure","message":"command terminated with '
        b'non-zero exit code","reason":"NonZeroExitCode","details":{"causes":'
        b'[{"reason":"ExitCode","message":"' + str(returncode).encode() + b'"}]}}'
    )


def make_ws_client(
    stdout_frames: Sequence[bytes] = (),
    stderr_frames: Sequence[bytes] = (),
    status: bytes | None = SUCCESS_STATUS,
    error_after_close: Exception | None = None,
) -> WSClient:
    """Create a mock WSClient whose socket delivers the given channel frames.

    The stdout and stderr frames are interleaved pairwise, as the server
    interleaves the two channels, and are followed by the status frame and the
    websocket close frame.

    `error_after_close` is raised by any read which happens after the caller has
    closed the client, as a read of a closed socket would.
    """
    frames: list[tuple[int, bytes]] = []
    for index in range(max(len(stdout_frames), len(stderr_frames))):
        if index < len(stderr_frames):
            frames.append(_data_frame(STDERR_CHANNEL, stderr_frames[index]))
        if index < len(stdout_frames):
            frames.append(_data_frame(STDOUT_CHANNEL, stdout_frames[index]))
    if status is not None:
        frames.append(_data_frame(ERROR_CHANNEL, status))
    frames.append((ABNF.OPCODE_CLOSE, b"\x03\xe8"))

    ws = MagicMock(spec=WSClient)
    closed = False

    def close(**kwargs: object) -> None:
        nonlocal closed
        closed = True

    def recv_data_frame(control_frame: bool = False) -> tuple[int, Any]:
        if closed and error_after_close is not None:
            raise error_after_close
        if not frames:
            raise AssertionError("The fake socket has no frames left to deliver.")
        op_code, data = frames.pop(0)
        return op_code, SimpleNamespace(data=data)

    ws.close.side_effect = close
    ws.is_open.side_effect = lambda: not closed
    ws.sock = MagicMock()
    ws.sock.recv_data_frame.side_effect = recv_data_frame
    return ws


def make_dead_ws_client(error: Exception) -> WSClient:
    """Create a mock WSClient whose socket raises on the first read."""
    ws = MagicMock(spec=WSClient)
    ws.is_open.return_value = True
    ws.sock = MagicMock()
    ws.sock.recv_data_frame.side_effect = error
    return ws


def _data_frame(channel: int, payload: bytes) -> tuple[int, bytes]:
    return ABNF.OPCODE_BINARY, bytes([channel]) + payload

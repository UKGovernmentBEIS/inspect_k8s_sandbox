from unittest.mock import MagicMock

import pytest
from kubernetes.stream.ws_client import WSClient  # type: ignore[import-untyped]

import k8s_sandbox._pod.op as op_module
from k8s_sandbox._pod.op import PodOperation


class _ConcretePodOp(PodOperation):
    """A concrete PodOperation so the base-class helper can be exercised."""


def _make_op() -> _ConcretePodOp:
    return _ConcretePodOp(MagicMock())


@pytest.mark.parametrize(
    ("chunk_size", "data", "expected"),
    [
        (4, b"abcdefghij", [b"abcd", b"efgh", b"ij"]),
        (1024, b"hello", [b"hello"]),
        (4, "abcdefg", ["abcd", "efg"]),  # str in -> str frames out
        (4, b"", []),
        (4, b"abcdefgh", [b"abcd", b"efgh"]),  # exact multiple, no trailing write
    ],
)
def test_write_stdin_chunked(
    monkeypatch: pytest.MonkeyPatch,
    chunk_size: int,
    data: str | bytes,
    expected: list[bytes | str],
) -> None:
    monkeypatch.setattr(op_module, "_STDIN_CHUNK_SIZE", chunk_size)
    ws = MagicMock(spec=WSClient)

    _make_op()._write_stdin_chunked(ws, data)

    assert [call.args[0] for call in ws.write_stdin.call_args_list] == expected


def test_write_stdin_chunked_default_chunk_size_splits_oversized_payload() -> None:
    # Regression guard: a payload one byte over the chunk size must split into
    # more than one frame.
    ws = MagicMock(spec=WSClient)

    _make_op()._write_stdin_chunked(ws, b"x" * (op_module._STDIN_CHUNK_SIZE + 1))

    assert op_module._STDIN_CHUNK_SIZE == 1024**2
    assert ws.write_stdin.call_count == 2
    assert len(ws.write_stdin.call_args_list[0].args[0]) == op_module._STDIN_CHUNK_SIZE

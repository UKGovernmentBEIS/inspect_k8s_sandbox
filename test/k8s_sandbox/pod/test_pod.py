from unittest.mock import MagicMock

from aisitools.k8s_sandbox._pod.execute import ExecuteOperation


def test_filter_sentinel_and_returncode():
    executor = ExecuteOperation(MagicMock())
    frame = b"before<completed-sentinel-value-42>after"

    assert executor._filter_sentinel_and_returncode(frame) == (b"beforeafter", 42)


def test_filter_sentinel_and_returncode_new_lines():
    executor = ExecuteOperation(MagicMock())
    frame = b"a\nb<completed-sentinel-value-42>\nc\nd"

    assert executor._filter_sentinel_and_returncode(frame) == (b"a\nb\nc\nd", 42)


def test_filter_sentinel_and_returncode_not_present():
    executor = ExecuteOperation(MagicMock())
    frame = b"stdout"

    assert executor._filter_sentinel_and_returncode(frame) == (b"stdout", None)


def test_filter_sentinel_and_returncode_empty():
    executor = ExecuteOperation(MagicMock())
    frame = b""

    assert executor._filter_sentinel_and_returncode(frame) == (b"", None)


def test_filter_sentinel_and_returncode_nothing_preceeding():
    executor = ExecuteOperation(MagicMock())
    frame = b"<completed-sentinel-value-42>after"

    assert executor._filter_sentinel_and_returncode(frame) == (b"after", 42)


def test_filter_sentinel_and_returncode_nothing_following():
    executor = ExecuteOperation(MagicMock())
    frame = b"before<completed-sentinel-value-42>"

    assert executor._filter_sentinel_and_returncode(frame) == (b"before", 42)


def test_filter_sentinel_and_returncode_0():
    executor = ExecuteOperation(MagicMock())
    frame = b"<completed-sentinel-value-0>"

    assert executor._filter_sentinel_and_returncode(frame) == (b"", 0)


def test_filter_sentinel_and_returncode_255():
    executor = ExecuteOperation(MagicMock())
    frame = b"<completed-sentinel-value-255>"

    assert executor._filter_sentinel_and_returncode(frame) == (b"", 255)

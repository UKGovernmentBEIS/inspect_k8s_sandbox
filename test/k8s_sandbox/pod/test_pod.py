from unittest.mock import MagicMock

from k8s_sandbox._pod.execute import ExecuteOperation


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


def test_build_idempotent_shell_script_contains_marker():
    executor = ExecuteOperation(MagicMock())
    exec_id = "test-exec-id-123"
    script = executor._build_idempotent_shell_script(
        command=["echo", "hello"],
        stdin=None,
        cwd=None,
        env={},
        timeout=None,
        execution_id=exec_id,
    )
    # Should create marker file before command
    assert f"/tmp/.k8s_exec_{exec_id}.marker" in script
    # Should write status after command
    assert f"/tmp/.k8s_exec_{exec_id}.status" in script
    # Original command should be present
    assert "echo hello" in script


def test_build_idempotent_shell_script_with_cwd_and_env():
    executor = ExecuteOperation(MagicMock())
    exec_id = "test-exec-id-456"
    script = executor._build_idempotent_shell_script(
        command=["ls", "-la"],
        stdin=None,
        cwd="/tmp",
        env={"FOO": "bar"},
        timeout=10,
        execution_id=exec_id,
    )
    assert "cd /tmp" in script
    assert "export FOO=bar" in script
    assert f"/tmp/.k8s_exec_{exec_id}.marker" in script

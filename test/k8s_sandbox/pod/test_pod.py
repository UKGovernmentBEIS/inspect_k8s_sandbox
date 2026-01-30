import os
from unittest.mock import MagicMock, patch

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


def test_pod_restart_check_disabled_skips_api_call():
    """Test that INSPECT_POD_RESTART_CHECK=false skips the restart check."""
    executor = ExecuteOperation(MagicMock())

    # When env var is false, should not call k8s_client
    with patch.dict(os.environ, {"INSPECT_POD_RESTART_CHECK": "false"}):
        with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
            executor._check_for_pod_restart()  # pyright: ignore[reportPrivateUsage]
            mock_client.assert_not_called()


def test_pod_restart_check_disabled_case_insensitive():
    """Test that INSPECT_POD_RESTART_CHECK=false is case insensitive."""
    executor = ExecuteOperation(MagicMock())

    for value in ["FALSE", "False", "false"]:
        with patch.dict(os.environ, {"INSPECT_POD_RESTART_CHECK": value}):
            with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
                executor._check_for_pod_restart()  # pyright: ignore[reportPrivateUsage]
                mock_client.assert_not_called()


def test_pod_restart_check_enabled_by_default():
    """Test that restart check runs by default (env var not set)."""
    executor = ExecuteOperation(MagicMock())

    # Remove the env var if it exists
    env = os.environ.copy()
    _ = env.pop("INSPECT_POD_RESTART_CHECK", None)

    with patch.dict(os.environ, env, clear=True):
        with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
            # Will fail because mock isn't fully set up, but we just want to
            # verify k8s_client was called
            try:
                executor._check_for_pod_restart()  # pyright: ignore[reportPrivateUsage]
            except Exception:
                pass
            mock_client.assert_called_once()

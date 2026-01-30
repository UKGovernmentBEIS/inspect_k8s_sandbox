from unittest.mock import MagicMock, patch

import pytest
from websocket import WebSocketConnectionClosedException

from k8s_sandbox._pod.execute import ExecuteOperation


class TestExecRetry:
    """Tests for exec retry logic."""

    def test_exec_succeeds_first_attempt(self):
        """Exec should return result without retry if first attempt succeeds."""
        pod_info = MagicMock()
        executor = ExecuteOperation(pod_info)

        with patch.object(executor, "_exec_with_idempotency") as mock_exec:
            with patch.object(executor, "_check_for_pod_restart"):
                mock_exec.return_value = MagicMock(
                    returncode=0, stdout="output", stderr=""
                )

                result = executor.exec(["echo", "hello"], None, None, {}, None, None)

                assert result.returncode == 0
                assert mock_exec.call_count == 1

    def test_exec_retries_on_connection_closed(self):
        """Exec should retry when WebSocket connection is closed."""
        pod_info = MagicMock()
        executor = ExecuteOperation(pod_info)

        with patch.object(executor, "_exec_with_idempotency") as mock_exec:
            with patch.object(executor, "_check_execution_state") as mock_check:
                with patch.object(executor, "_check_for_pod_restart"):
                    with patch.object(executor, "_cleanup_marker_files"):
                        # First call fails, second succeeds
                        mock_exec.side_effect = [
                            WebSocketConnectionClosedException("Connection lost"),
                            MagicMock(returncode=0, stdout="output", stderr=""),
                        ]
                        mock_check.return_value = "not_started"

                        result = executor.exec(
                            ["echo", "hello"], None, None, {}, None, None
                        )

                        assert result.returncode == 0
                        assert mock_exec.call_count == 2

    def test_exec_does_not_retry_if_command_started(self):
        """Exec should not retry if command already started executing."""
        pod_info = MagicMock()
        executor = ExecuteOperation(pod_info)

        with patch.object(executor, "_exec_with_idempotency") as mock_exec:
            with patch.object(executor, "_check_execution_state") as mock_check:
                with patch.object(executor, "_check_for_pod_restart"):
                    mock_exec.side_effect = WebSocketConnectionClosedException(
                        "Connection lost"
                    )
                    # Command started, unsafe to retry
                    mock_check.return_value = "started"

                    with pytest.raises(WebSocketConnectionClosedException):
                        executor.exec(["echo", "hello"], None, None, {}, None, None)

                    assert mock_exec.call_count == 1  # No retry

    def test_exec_raises_after_max_retries(self):
        """Exec should raise after exhausting retries."""
        pod_info = MagicMock()
        executor = ExecuteOperation(pod_info)

        with patch.object(executor, "_exec_with_idempotency") as mock_exec:
            with patch.object(executor, "_check_execution_state") as mock_check:
                with patch.object(executor, "_check_for_pod_restart"):
                    # Don't actually sleep in tests
                    with patch("time.sleep"):
                        mock_exec.side_effect = WebSocketConnectionClosedException(
                            "Connection lost"
                        )
                        mock_check.return_value = "not_started"

                        with pytest.raises(WebSocketConnectionClosedException):
                            executor.exec(["echo", "hello"], None, None, {}, None, None)

                        # Default is 3 retries + 1 initial = 4 attempts
                        assert mock_exec.call_count == 4

    def test_exec_calls_cleanup_on_success(self):
        """Cleanup should be called after successful execution."""
        pod_info = MagicMock()
        executor = ExecuteOperation(pod_info)

        with patch.object(executor, "_exec_with_idempotency") as mock_exec:
            with patch.object(executor, "_cleanup_marker_files") as mock_cleanup:
                with patch.object(executor, "_check_for_pod_restart"):
                    mock_exec.return_value = MagicMock(
                        returncode=0, stdout="output", stderr=""
                    )

                    result = executor.exec(
                        ["echo", "hello"], None, None, {}, None, None
                    )

                    assert result.returncode == 0
                    mock_cleanup.assert_called_once()

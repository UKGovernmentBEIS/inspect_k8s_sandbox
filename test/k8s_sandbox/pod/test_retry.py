import ssl

from websocket import WebSocketBadStatusException, WebSocketConnectionClosedException

from k8s_sandbox._pod.retry import (
    ExecutionState,
    RetryContext,
    generate_execution_id,
    get_marker_paths,
    is_retryable_error,
)


def test_websocket_connection_closed_is_retryable():
    error = WebSocketConnectionClosedException("Connection to remote host was lost")
    assert is_retryable_error(error) is True


def test_ssl_eof_error_is_retryable():
    error = ssl.SSLEOFError("EOF occurred in violation of protocol")
    assert is_retryable_error(error) is True


def test_websocket_bad_status_500_is_retryable():
    error = WebSocketBadStatusException("Handshake status 500", 500)
    assert is_retryable_error(error) is True


def test_websocket_bad_status_pod_not_found_is_not_retryable():
    error = WebSocketBadStatusException("pod does not exist", 404)
    assert is_retryable_error(error) is False


def test_websocket_bad_status_container_not_found_is_not_retryable():
    error = WebSocketBadStatusException("container not found", 404)
    assert is_retryable_error(error) is False


def test_websocket_bad_status_400_is_not_retryable():
    error = WebSocketBadStatusException("Bad request", 400)
    assert is_retryable_error(error) is False


def test_generic_exception_is_not_retryable():
    error = RuntimeError("Some other error")
    assert is_retryable_error(error) is False


def test_none_is_not_retryable():
    assert is_retryable_error(None) is False


def test_retry_context_initial_state():
    ctx = RetryContext(max_retries=3)
    assert ctx.attempt == 0
    assert ctx.max_retries == 3
    assert ctx.should_retry is True


def test_retry_context_after_max_retries():
    ctx = RetryContext(max_retries=2)
    ctx.attempt = 2
    assert ctx.should_retry is False


def test_retry_context_increment():
    ctx = RetryContext(max_retries=3)
    ctx.increment()
    assert ctx.attempt == 1
    ctx.increment()
    assert ctx.attempt == 2


def test_retry_context_delay_exponential_backoff():
    ctx = RetryContext(max_retries=3, base_delay=1.0, max_delay=10.0)
    ctx.attempt = 1
    delay1 = ctx.get_delay()
    assert 1.0 <= delay1 <= 2.0  # 1.0 * (1 + jitter up to 1.0)

    ctx.attempt = 2
    delay2 = ctx.get_delay()
    assert 2.0 <= delay2 <= 4.0  # 2.0 * (1 + jitter up to 1.0)

    ctx.attempt = 3
    delay3 = ctx.get_delay()
    assert 4.0 <= delay3 <= 8.0  # 4.0 * (1 + jitter up to 1.0)


def test_generate_execution_id_format():
    exec_id = generate_execution_id()
    # Should be a UUID-like string
    assert len(exec_id) == 36
    assert exec_id.count("-") == 4


def test_generate_execution_id_unique():
    ids = {generate_execution_id() for _ in range(100)}
    assert len(ids) == 100


def test_get_marker_paths():
    exec_id = "abc-123"
    marker, status = get_marker_paths(exec_id)
    assert marker == "/tmp/.k8s_exec_abc-123.marker"
    assert status == "/tmp/.k8s_exec_abc-123.status"


def test_execution_state_values():
    assert ExecutionState.NOT_STARTED.value == "not_started"
    assert ExecutionState.STARTED.value == "started"
    assert ExecutionState.COMPLETED.value == "completed"

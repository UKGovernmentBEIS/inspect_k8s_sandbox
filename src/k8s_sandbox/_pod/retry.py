"""Retry logic for transient WebSocket connection errors."""

import random
import ssl
import uuid
from dataclasses import dataclass, field
from enum import Enum

from websocket import WebSocketBadStatusException, WebSocketConnectionClosedException


@dataclass
class RetryContext:
    """Context for tracking retry attempts with exponential backoff.

    Attributes:
        max_retries: Maximum number of retry attempts.
        base_delay: Base delay in seconds for exponential backoff.
        max_delay: Maximum delay in seconds.
        attempt: Current attempt number (0-indexed).
    """

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 10.0
    attempt: int = field(default=0, init=False)

    @property
    def should_retry(self) -> bool:
        """Return True if more retry attempts are available."""
        return self.attempt < self.max_retries

    def increment(self) -> None:
        """Increment the attempt counter."""
        self.attempt += 1

    def get_delay(self) -> float:
        """Calculate delay with exponential backoff and jitter.

        Returns:
            Delay in seconds before next retry attempt.
        """
        # Exponential backoff: base_delay * 2^(attempt-1)
        delay = self.base_delay * (2 ** (self.attempt - 1))
        # Add jitter: random value between 0 and delay
        jitter = random.uniform(0, delay)
        return min(delay + jitter, self.max_delay)


def is_retryable_error(error: BaseException | None) -> bool:
    """Determine if an error is a transient connection error that can be retried.

    Args:
        error: The exception to check.

    Returns:
        True if the error is transient and the operation can be retried.
    """
    if error is None:
        return False

    # WebSocket connection was closed unexpectedly
    if isinstance(error, WebSocketConnectionClosedException):
        return True

    # SSL connection terminated unexpectedly
    if isinstance(error, ssl.SSLEOFError):
        return True

    # WebSocket handshake failed with server error (500)
    # But NOT for "pod does not exist" or "container not found" which are permanent
    if isinstance(error, WebSocketBadStatusException):
        message = str(error).lower()
        if "pod does not exist" in message or "container not found" in message:
            return False
        # Only retry 5xx server errors
        if hasattr(error, "status_code") and error.status_code >= 500:
            return True

    return False


def generate_execution_id() -> str:
    """Generate a unique execution ID for tracking command execution.

    Returns:
        A UUID string suitable for use as a marker file name.
    """
    return str(uuid.uuid4())


class ExecutionState(Enum):
    """State of command execution based on marker files."""

    NOT_STARTED = "not_started"
    STARTED = "started"
    COMPLETED = "completed"


def get_marker_paths(execution_id: str) -> tuple[str, str]:
    """Get marker and status file paths for an execution ID.

    Args:
        execution_id: Unique execution ID.

    Returns:
        Tuple of (marker_path, status_path).
    """
    marker = f"/tmp/.k8s_exec_{execution_id}.marker"
    status = f"/tmp/.k8s_exec_{execution_id}.status"
    return marker, status

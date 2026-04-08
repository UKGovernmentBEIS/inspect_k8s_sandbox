"""Structured logging with Inspect trace integration.

Delegates formatting to k8s_sandbox_core._logger, adds inspect_ai trace_action.
"""

import logging
from contextlib import contextmanager
from typing import Any, Generator

from inspect_ai.util import trace_action, trace_message

from k8s_sandbox_core._logger import (
    _format_kwargs_as_json,
    format_log_message,
    log_debug,
    log_error,
    log_warn,
)

logger = logging.getLogger(__name__)

# Re-export core utilities unchanged
__all__ = [
    "format_log_message",
    "log_debug",
    "log_error",
    "log_warn",
    "log_trace",
    "inspect_trace_action",
]


def log_trace(message: str, **kwargs: Any) -> None:
    """Format and log a message at TRACE level with K8s category.

    Uses Inspect's trace_message for structured trace output.
    """
    formatted = format_log_message(message, **kwargs)
    trace_message(logger, category="K8s", message=formatted)


@contextmanager
def inspect_trace_action(action: str, **kwargs: Any) -> Generator[None, None, None]:
    """Context manager that traces an action using Inspect's trace_action."""
    json_kwargs = _format_kwargs_as_json(**kwargs)
    with trace_action(logger, action, json_kwargs):
        yield

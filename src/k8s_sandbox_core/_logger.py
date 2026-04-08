"""Structured logging utilities for k8s_sandbox_core.

Same formatting as k8s_sandbox._logger but without inspect_ai trace integration.
"""

import json
import logging
import os
from contextlib import contextmanager
from typing import Any, Generator

logger = logging.getLogger(__name__)

TRUNCATED_SUFFIX = "...<truncated-for-logging>"
DEFAULT_ARG_TRUNCATION_THRESHOLD = 1000


def log_trace(message: str, **kwargs: Any) -> None:
    """Format and log a message at DEBUG level with K8s category."""
    formatted = format_log_message(message, **kwargs)
    logger.debug(f"K8s: {formatted}")


def log_debug(message: str, **kwargs: Any) -> None:
    """Format and log a message at DEBUG level with K8s prefix."""
    formatted = format_log_message(message, **kwargs)
    logger.debug(f"K8s: {formatted}")


def log_error(message: str, **kwargs: Any) -> None:
    """Format and log a message at ERROR level with K8s prefix."""
    formatted = format_log_message(message, **kwargs)
    logger.error(f"K8s: {formatted}")


def log_warn(message: str, **kwargs: Any) -> None:
    """Format and log a message at WARNING level with K8s prefix."""
    formatted = format_log_message(message, **kwargs)
    logger.warning(f"K8s: {formatted}")


def format_log_message(message: str, **kwargs: Any) -> str:
    """Format message in a structured fashion."""
    if not kwargs:
        return message
    json_kwargs = _format_kwargs_as_json(**kwargs)
    return f"{message} {json_kwargs}"


@contextmanager
def inspect_trace_action(action: str, **kwargs: Any) -> Generator[None, None, None]:
    """No-op context manager replacing inspect's trace_action."""
    yield


def _truncate_arg(arg: Any) -> str:
    arg_str = str(arg)
    truncation_threshold = _get_arg_truncation_threshold()
    if len(arg_str) > truncation_threshold:
        return arg_str[:truncation_threshold] + TRUNCATED_SUFFIX
    return arg_str


def _get_arg_truncation_threshold() -> int:
    try:
        return int(os.environ["INSPECT_K8S_LOG_TRUNCATION_THRESHOLD"])
    except (KeyError, ValueError):
        return DEFAULT_ARG_TRUNCATION_THRESHOLD


def _format_kwargs_as_json(**kwargs: Any) -> str:
    truncated_kwargs = {k: _truncate_arg(v) for k, v in kwargs.items()}
    return json.dumps(truncated_kwargs, ensure_ascii=False)

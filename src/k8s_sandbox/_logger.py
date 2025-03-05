import json
import logging
import os
from contextlib import contextmanager
from typing import Any, Generator

from inspect_ai.util import trace_action, trace_message

logger = logging.getLogger(__name__)

TRUNCATED_SUFFIX = "...<truncated-for-logging>"
# The threshold at which to truncate individual arguments in logging messages.
# Some ExecResults can contain very large outputs.
DEFAULT_ARG_TRUNCATION_THRESHOLD = 1000


def log_trace(message: str, **kwargs: Any) -> None:
    """Format and log a message at TRACE level with K8s category.

    Args:
        message: The log message.
        **kwargs: Key-value pairs to include in the log message. Values are truncated if
          they exceed DEFAULT_ARG_TRUNCATION_THRESHOLD (which can be overridden with env
          var INSPECT_K8S_LOG_TRUNCATION_THRESHOLD).
    """
    formatted = format_log_message(message, **kwargs)
    trace_message(logger, category="K8s", message=formatted)


def log_warning(message: str, **kwargs: Any) -> None:
    """Format and log a message at WARNING level with K8s prefix.

    Args:
        message: The log message.
        **kwargs: Key-value pairs to include in the log message. Values are truncated if
          they exceed DEFAULT_ARG_TRUNCATION_THRESHOLD (which can be overridden with env
          var INSPECT_K8S_LOG_TRUNCATION_THRESHOLD).
    """
    formatted = format_log_message(message, **kwargs)
    logger.error(f"K8s: {formatted}")


def log_error(message: str, **kwargs: Any) -> None:
    """Format and log a message at ERROR level with K8s prefix.

    Args:
        message: The log message.
        **kwargs: Key-value pairs to include in the log message. Values are truncated if
          they exceed DEFAULT_ARG_TRUNCATION_THRESHOLD (which can be overridden with env
          var INSPECT_K8S_LOG_TRUNCATION_THRESHOLD).
    """
    formatted = format_log_message(message, **kwargs)
    logger.error(f"K8s: {formatted}")


def format_log_message(message: str, **kwargs: Any) -> str:
    """Format message in a structured fashion.

    Args:
        message: The log message.
        **kwargs: Key-value pairs to include in the log message. Values are truncated if
          they exceed DEFAULT_ARG_TRUNCATION_THRESHOLD (which can be overridden with env
          var INSPECT_K8S_LOG_TRUNCATION_THRESHOLD).
    """
    if not kwargs:
        return message
    json_kwargs = _format_kwargs_as_json(**kwargs)
    return f"{message} {json_kwargs}"


@contextmanager
def inspect_trace_action(action: str, **kwargs: Any) -> Generator[None, None, None]:
    """Context manager that traces an action with structured logging.

    Uses Inspect's trace_action.

    Args:
        action: The action being performed (e.g. "K8s execute command in Pod").
        **kwargs: Key-value pairs to include in as details parameter to trace_action.
          Values are truncated if they exceed DEFAULT_ARG_TRUNCATION_THRESHOLD (which
          can be overridden with env var INSPECT_K8S_LOG_TRUNCATION_THRESHOLD).
    """
    json_kwargs = _format_kwargs_as_json(**kwargs)
    with trace_action(logger, action, json_kwargs):
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

import json
import logging
import os
from typing import Any

# TODO: We're accessing an internal constant. Can this be made public by inspect?
from inspect_ai._util.constants import SANDBOX

logger = logging.getLogger(__name__)

TRUNCATED_SUFFIX = "...<truncated-for-logging>"
# The threshold at which to truncate individual arguments in logging messages.
# Some ExecResults can contain very large outputs.
DEFAULT_ARG_TRUNCATION_THRESHOLD = 1000


def sandbox_log(message: str, level: int = SANDBOX, **kwargs: Any) -> None:
    """Format and log a message with "K8S: " prefix.

    Args:
        message: The log message.
        level: The log level. Defaults to SANDBOX.
        **kwargs: Key-value pairs to include in the log message. Values are truncated if
          they exceed DEFAULT_ARG_TRUNCATION_THRESHOLD (which can be overridden with env
          var INSPECT_K8S_LOG_TRUNCATION_THRESHOLD).
    """
    formatted = format_log_message(message, **kwargs)
    logger.log(level, f"K8S: {formatted}")


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
    truncated_kwargs = {k: _truncate_arg(v) for k, v in kwargs.items()}
    json_args = json.dumps(truncated_kwargs, ensure_ascii=False)
    return f"{message} {json_args}"


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

from contextlib import contextmanager
from typing import Generator

import pytest
from pytest import MonkeyPatch

from k8s_sandbox import _logger
from k8s_sandbox._logger import format_log_message


@pytest.fixture
def str_2000_chars() -> str:
    return "0123456789" * 200


def test_format_log_message() -> None:
    result = format_log_message("My message.")

    assert result == "My message."


def test_format_log_message_with_kwargs() -> None:
    result = format_log_message("My message.", a="1", b="2", c="3")

    assert result == 'My message. {"a": "1", "b": "2", "c": "3"}'


def test_format_log_message_with_non_str_kwargs() -> None:
    result = format_log_message("My message.", a=1, b=2.0, c=Exception("3"))

    assert result == 'My message. {"a": "1", "b": "2.0", "c": "3"}'


def test_format_log_message_truncates_values(str_2000_chars: str) -> None:
    result = format_log_message("My message.", my_value=str_2000_chars)

    assert len(result) < 1100
    assert result.endswith('...<truncated-for-logging>"}')


def test_format_log_message_escapes_values() -> None:
    value = "'\"\\"
    result = format_log_message("My message.", my_value=value)

    assert result == 'My message. {"my_value": "\'\\"\\\\"}'


def test_format_log_message_non_ascii() -> None:
    value = "日本語😀"
    result = format_log_message("My message.", my_value=value)

    assert result == 'My message. {"my_value": "日本語😀"}'


def test_truncation_threshold_is_loaded_from_env_var(
    str_2000_chars: str, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("INSPECT_K8S_LOG_TRUNCATION_THRESHOLD", "100")

    result = format_log_message("My message.", myvalue=str_2000_chars)

    assert len(result) < 200


def test_truncation_threshold_with_invalid_env_var(
    str_2000_chars: str, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("INSPECT_K8S_LOG_TRUNCATION_THRESHOLD", "invalid")

    result = format_log_message("My message.", myvalue=str_2000_chars)

    assert 1000 < len(result) < 1100


def test_truncation_threshold_with_unset_env_var(
    str_2000_chars: str, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.delenv("INSPECT_K8S_LOG_TRUNCATION_THRESHOLD", raising=False)

    result = format_log_message("My message.", myvalue=str_2000_chars)

    assert 1000 < len(result) < 1100


class _ExplodingArg:
    """Raises if anything tries to stringify it.

    Formatting calls str() on every value, so using this as a kwarg turns "was the
    argument formatted?" into a hard failure rather than something a mock might miss.
    """

    def __str__(self) -> str:  # pragma: no cover - must never be called
        raise AssertionError("argument was formatted despite the log level being off")

    __repr__ = __str__


def test_trace_action_does_not_format_when_trace_disabled(
    monkeypatch: MonkeyPatch,
) -> None:
    """The hot path: this wraps EVERY pod operation via _log_op.

    A production runner burned ~1 core of GIL formatting kwargs that a disabled TRACE
    logger discarded, starving the asyncio event loop until it stopped answering its
    Inspect control channel.
    """
    monkeypatch.setattr(_logger.logger, "isEnabledFor", lambda level: False)

    with _logger.inspect_trace_action("K8s some op", huge=_ExplodingArg()):
        pass


def test_log_debug_does_not_format_when_debug_disabled(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(_logger.logger, "isEnabledFor", lambda level: False)

    _logger.log_debug("My message.", huge=_ExplodingArg())


def test_log_trace_does_not_format_when_trace_disabled(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(_logger.logger, "isEnabledFor", lambda level: False)

    _logger.log_trace("My message.", huge=_ExplodingArg())


def test_trace_action_still_formats_when_trace_enabled(
    monkeypatch: MonkeyPatch,
) -> None:
    """Gating must not silently drop detail when tracing is actually on."""
    seen: list[str] = []
    monkeypatch.setattr(_logger.logger, "isEnabledFor", lambda level: True)
    monkeypatch.setattr(
        _logger, "trace_action", lambda logger, action, message: _noop_cm(seen, message)
    )

    with _logger.inspect_trace_action("K8s some op", a="1"):
        pass

    assert seen == ['{"a": "1"}']


@contextmanager
def _noop_cm(seen: list[str], message: str) -> Generator[None, None, None]:
    seen.append(message)
    yield

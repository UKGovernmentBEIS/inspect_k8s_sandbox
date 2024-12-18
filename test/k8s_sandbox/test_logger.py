import pytest
from pytest import MonkeyPatch

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

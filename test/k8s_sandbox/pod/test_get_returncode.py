from unittest.mock import MagicMock

import pytest
import yaml
from kubernetes.stream.ws_client import WSClient  # type: ignore

from aisitools.k8s_sandbox._pod.get_returncode import (
    GetReturncodeError,
    get_returncode,
)


@pytest.fixture
def mock_response() -> WSClient:
    mock_client: WSClient = MagicMock()
    mock_client.is_open.return_value = False
    return mock_client


def test_get_returncode_with_none(mock_response: WSClient) -> None:
    mock_response.read_channel.return_value = None

    with pytest.raises(GetReturncodeError) as e:
        get_returncode(mock_response)

    assert "because it was empty" in str(e.value)


def test_get_returncode_with_empty_response(mock_response: WSClient) -> None:
    mock_response.read_channel.return_value = ""

    with pytest.raises(GetReturncodeError) as e:
        get_returncode(mock_response)

    assert "because it was empty" in str(e.value)


def test_get_returncode_with_empty_yaml_response(mock_response: WSClient) -> None:
    mock_response.read_channel.return_value = yaml.dump({})

    with pytest.raises(GetReturncodeError) as e:
        get_returncode(mock_response)

    assert "because it did not contain a `status` key" in str(e.value)


def test_get_returncode_with_no_status(mock_response: WSClient) -> None:
    mock_response.read_channel.return_value = yaml.dump({"metadata": {}, "foo": "bar"})

    with pytest.raises(GetReturncodeError) as e:
        get_returncode(mock_response)

    assert "because it did not contain a `status` key" in str(e.value)


def test_get_returncode_with_real_success_response(mock_response: WSClient) -> None:
    mock_response.read_channel.return_value = yaml.dump(
        {"metadata": {}, "status": "Success"}
    )

    assert get_returncode(mock_response) == 0


def test_get_returncode_failure(mock_response: WSClient) -> None:
    mock_response.read_channel.return_value = yaml.dump(
        {
            "status": "Failure",
            "details": {
                "causes": [
                    {"reason": "Foo", "message": "bar"},
                    {"reason": "ExitCode", "message": "42"},
                ]
            },
        }
    )

    assert get_returncode(mock_response) == 42


def test_get_returncode_real_non_zero_response(mock_response: WSClient) -> None:
    mock_response.read_channel.return_value = yaml.dump(
        {
            "metadata": {},
            "status": "Failure",
            "message": "command terminated with non-zero exit code: error executing command [bash -c timeout 1 sleep 10], exit code 124",  # noqa: E501
            "reason": "NonZeroExitCode",
            "details": {"causes": [{"reason": "ExitCode", "message": "124"}]},
        }
    )

    assert get_returncode(mock_response) == 124


def test_get_returncode_real_no_exit_code_response(mock_response: WSClient) -> None:
    mock_response.read_channel.return_value = yaml.dump(
        {
            "metadata": {},
            "status": "Failure",
            "message": 'Internal error occurred: error executing command in container: failed to exec in container: failed to create exec "d5b4b8c74fd16c6f74b048f8d4349110071dd60c16867a057c77212115c319a7": cannot exec in a stopped state: unknown',  # noqa: E501
            "reason": "InternalError",
            "details": {
                "causes": [
                    {
                        "message": 'error executing command in container: failed to exec in container: failed to create exec "d5b4b8c74fd16c6f74b048f8d4349110071dd60c16867a057c77212115c319a7": cannot exec in a stopped state: unknown'  # noqa: E501
                    }
                ]
            },
            "code": 500,
        }
    )

    with pytest.raises(GetReturncodeError) as e:
        get_returncode(mock_response)

    assert "no entry in `details.causes` with `reason`=='ExitCode'" in str(e.value)


def test_get_returncode_when_response_is_open(mock_response: WSClient) -> None:
    mock_response.is_open.return_value = True

    with pytest.raises(AssertionError):
        get_returncode(mock_response)

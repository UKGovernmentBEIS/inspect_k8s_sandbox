import yaml
from kubernetes.stream.ws_client import (  # type: ignore[import-untyped]
    ERROR_CHANNEL,
    WSClient,
)

from k8s_sandbox._pod.error import ExecutableNotFoundError, GetReturncodeError


def get_returncode(ws_client: WSClient) -> int:
    """
    Extracts the returncode from a websocket client.

    Similar to the `WSClient.returncode` property, but with additional and more
    informative error handling.
    """
    assert not ws_client.is_open(), "ws_client must be closed to get return code."
    # Note: ERROR_CHANNEL is not the same as stderr. Aka status channel.
    return parse_returncode(ws_client.read_channel(ERROR_CHANNEL))


def parse_returncode(channel_value: str | bytes | None) -> int:
    """Extracts the returncode from the value of the error (aka status) channel.

    For callers which read the channel themselves rather than letting the
    `WSClient` buffer it.
    """
    if not channel_value:
        raise GetReturncodeError(
            "Failed to get returncode from k8s error channel because it was empty."
        )
    if isinstance(channel_value, bytes):
        channel_value = channel_value.decode("utf-8", errors="replace")
    loaded = yaml.safe_load(channel_value)
    if "status" not in loaded:
        raise GetReturncodeError(
            "Failed to get returncode from k8s error channel because it did not "
            f"contain a `status` key. Error channel: {channel_value}",
        )
    if loaded["status"] == "Success":
        return 0
    for cause in loaded["details"]["causes"]:
        if cause.get("reason") == "ExitCode":
            return int(cause["message"])
    if "error finding executable" in loaded["message"]:
        raise ExecutableNotFoundError(loaded["message"])
    raise GetReturncodeError(
        "Failed to get returncode from k8s error channel because `status`!='Success' "
        "and there was no entry in `details.causes` with `reason`=='ExitCode'. "
        f"Error channel: {channel_value}",
    )

from typing import Any

from k8s_sandbox._logger import format_log_message


class PodError(Exception):
    """
    A generic error raised when interacting with a pod.

    This will typically cause the eval to fail.
    """

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(format_log_message(message, **kwargs))


class GetReturncodeError(Exception):
    """The return code of a pod operation could not be retrieved."""

    pass

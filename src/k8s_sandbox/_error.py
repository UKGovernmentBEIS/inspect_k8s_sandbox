"""Top-level exception type for k8s_sandbox operations."""

from typing import Any

from k8s_sandbox._logger import format_log_message


class K8sError(Exception):
    """An error that occurred during a Kubernetes operation.

    This will typically cause the eval to fail.
    """

    def __init__(self, message: str, **kwargs: Any):  # noqa: D107
        super().__init__(format_log_message(message, **kwargs))

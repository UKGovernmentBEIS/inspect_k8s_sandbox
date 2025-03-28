import logging
from abc import ABC
from dataclasses import dataclass
from typing import Generator

from kubernetes.stream import stream  # type: ignore
from kubernetes.stream.ws_client import WSClient  # type: ignore

from k8s_sandbox._kubernetes_api import k8s_client

# The duration to wait for an initial response from the k8s API server.
# The initial response is received before the command is necessarily complete, so
# long-running commands will not be affected by this timeout.
# https://github.com/kubernetes-client/python/blob/master/examples/watch/timeout-settings.md
API_TIMEOUT = 60

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PodInfo:
    """
    Information required to interact with a Kubernetes pod.

    This class is immutable and thread-safe.
    """

    name: str
    namespace: str
    context_name: str | None
    """The name of the kubeconfig context. If None, use the current context."""
    default_container_name: str


class PodOperation(ABC):
    """
    A base class for a synchronous operation on a pod.

    The purpose of splitting these operations into separate classes is to encapsulate
    and isolate their respective behaviour.
    """

    _failed_to_discard_duplicate_channel = False

    def __init__(self, pod: PodInfo):
        self._pod = pod

    def create_websocket_client_for_exec(
        self, **kwargs
    ) -> Generator[WSClient, None, None]:
        client = k8s_client(self._pod.context_name)
        # Note: ApiException is intentionally not caught; it should fail the eval.
        ws_client = stream(
            client.connect_get_namespaced_pod_exec,
            name=self._pod.name,
            namespace=self._pod.namespace,
            container=self._pod.default_container_name,
            _preload_content=False,
            # This is the timeout for the API request, not the command itself.
            _request_timeout=API_TIMEOUT,
            **kwargs,
        )
        try:
            self._discard_duplicate_channel(ws_client)
            yield ws_client
        finally:
            ws_client.close()

    def _discard_duplicate_channel(self, ws_client: WSClient) -> None:
        # Avoid issuing a warning multiple times.
        if PodOperation._failed_to_discard_duplicate_channel:
            return
        # WSClient stores all stdout and stderr in WSClient._all in addition to the
        # relevant channels. Set the _all channel to IgnoredIO to reduce memory usage.
        # https://github.com/kubernetes-client/python/issues/2302
        # Handle ImportError as we're importing a private class.
        try:
            from kubernetes.stream.ws_client import _IgnoredIO  # type: ignore
        except ImportError as e:
            logger.warning(
                f"Failed to set Kubernetes' WSClient._all channel to _IgnoredIO: {e}"
            )
            PodOperation._failed_to_discard_duplicate_channel = True
            return
        # Whilst we can set the _all attribute whether it exists or not, we should
        # log a warning if it doesn't exist as this may indicate a change in the
        # Kubernetes library.
        if not hasattr(ws_client, "_all"):
            logger.warning(
                "Failed to set Kubernetes' WSClient._all channel to _IgnoredIO: there "
                "was no _all attribute on the WSClient object."
            )
            PodOperation._failed_to_discard_duplicate_channel = True
            return
        ws_client._all = _IgnoredIO()


def raise_for_known_read_write_errors(stderr: str) -> None:
    # The Inspect Sandbox interface asks us to raise specific exceptions for recognised
    # error messages.
    casefolded = stderr.casefold()
    if "no such file or directory" in casefolded:
        raise FileNotFoundError(stderr)
    if "permission denied" in casefolded:
        raise PermissionError(stderr)
    if "is a directory" in casefolded:
        raise IsADirectoryError(stderr)

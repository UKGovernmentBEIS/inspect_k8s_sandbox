import json
import logging
import threading
from abc import ABC
from dataclasses import dataclass
from typing import Generator, Literal

from kubernetes.stream import stream  # type: ignore
from kubernetes.stream.ws_client import RESIZE_CHANNEL, WSClient  # type: ignore

from k8s_sandbox._kubernetes_api import k8s_client

# The duration to wait for an initial response from the k8s API server.
# The initial response is received before the command is necessarily complete, so
# long-running commands will not be affected by this timeout.
# https://github.com/kubernetes-client/python/blob/master/examples/watch/timeout-settings.md
API_TIMEOUT = 60

# Interval between WebSocket keepalive frames. Containerd's CRI streaming server
# enforces a stream_idle_timeout (default 4h [1]) that closes connections with no
# data activity. Sending a resize-channel data frame resets the idle timer via
# resetTimeout() in the server's wsstream conn.go read loop [2]. 30 seconds is well
# under any realistic idle timeout while adding negligible overhead.
#
# [1] https://github.com/kubernetes/kubernetes/blob/db9fcfeed29b860d8dd7188bc1903c4709977890/staging/src/k8s.io/kubelet/pkg/cri/streaming/server.go#L100-L105
# [2] https://github.com/kubernetes/kubernetes/blob/77b02b7ad40d36cd803856de5ba5922c947cb0aa/staging/src/k8s.io/apimachinery/pkg/util/httpstream/wsstream/conn.go#L348-L356
_KEEPALIVE_INTERVAL_SECONDS = 30

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
    uid: str
    initial_restart_count: int
    restarted_container_behavior: Literal["warn", "raise"]


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
        stop_keepalive = threading.Event()
        keepalive = threading.Thread(
            target=_send_keepalive,
            args=(ws_client, stop_keepalive),
            daemon=True,
            name="ws-keepalive",
        )
        try:
            self._discard_duplicate_channel(ws_client)
            keepalive.start()
            yield ws_client
        finally:
            stop_keepalive.set()
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

    def _check_for_pod_restart(self):
        client = k8s_client(self._pod.context_name)
        pod = client.read_namespaced_pod(
            name=self._pod.name, namespace=self._pod.namespace
        )
        if pod.metadata.uid != self._pod.uid:
            message = (
                f"Pod UID mismatch: expected {self._pod.uid}, got {pod.metadata.uid} "
                f"for {pod.metadata.name}"
            )
            if self._pod.restarted_container_behavior == "warn":
                logger.warning(message)
            else:
                raise RuntimeError(message)
        status = next(
            (
                container_status
                for container_status in pod.status.container_statuses
                if container_status.name == self._pod.default_container_name
            ),
            None,
        )
        if status is None:
            message = (
                f"Pod '{pod.metadata.name}' does not have a container named "
                f"'{self._pod.default_container_name}'"
            )
            if self._pod.restarted_container_behavior == "warn":
                logger.warning(message)
                return
            else:
                raise RuntimeError(message)
        if status.restart_count > self._pod.initial_restart_count:
            last_reason = (
                status.last_state.terminated.reason
                if status.last_state.terminated
                else "unknown"
            )
            message = (
                f"Container '{status.name}' in pod '{pod.metadata.name}' has restarted "
                f"{status.restart_count} time(s) (last reason: {last_reason}); "
                "pod state is no longer guaranteed."
            )
            if self._pod.restarted_container_behavior == "warn":
                logger.warning(message)
            else:
                raise RuntimeError(message)


def _send_keepalive(ws_client: WSClient, stop: threading.Event) -> None:
    """Send periodic resize-channel frames to prevent idle timeout.

    Containerd's CRI streaming server closes WebSocket connections that receive no
    data frames within stream_idle_timeout (default 4h). Standard WebSocket pings do
    NOT reset this timer because the Python kubernetes client negotiates the
    v4.channel.k8s.io subprotocol [1], whose server-side handler uses
    golang.org/x/net/websocket. That library's Receive() silently consumes ping/pong
    control frames in an internal loop [2][3] without returning to the caller, so the
    resetTimeout() call in wsstream/conn.go (which sits *before* Receive()) is never
    re-executed.

    Writing to the resize channel (channel 4) sends a real data frame that causes
    Receive() to return, triggering resetTimeout() [4]. The resize handler silently
    ignores the payload since no TTY is allocated for non-interactive exec sessions.

    [1] https://github.com/kubernetes-client/python/blob/6fb1fd723eeb8880626118aeb95ebb1a7c73d5ad/kubernetes/base/stream/ws_client.py#L468-L472
    [2] https://github.com/golang/net/blob/2914f46773171f4fa13e276df1135bafef677801/websocket/websocket.go#L339-L349
    [3] https://github.com/golang/net/blob/2914f46773171f4fa13e276df1135bafef677801/websocket/hybi.go#L290-L302
    [4] https://github.com/kubernetes/kubernetes/blob/77b02b7ad40d36cd803856de5ba5922c947cb0aa/staging/src/k8s.io/apimachinery/pkg/util/httpstream/wsstream/conn.go#L348-L356
    """
    payload = json.dumps({"Width": 80, "Height": 24}).encode()  # Size is arbitrary
    while not stop.wait(_KEEPALIVE_INTERVAL_SECONDS):
        try:
            if ws_client.is_open():
                ws_client.write_channel(RESIZE_CHANNEL, payload)
            else:
                break
        except Exception:
            logger.debug(
                "Failed to send k8s websocket keepalive frame, bailing out",
                exc_info=True,
            )
            break


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

import json
import logging
import socket
import threading
from abc import ABC
from dataclasses import dataclass
from typing import Generator, Literal

import websocket
from kubernetes.stream import stream  # type: ignore
from kubernetes.stream.ws_client import RESIZE_CHANNEL, WSClient  # type: ignore

from k8s_sandbox._kubernetes_api import k8s_client
from k8s_sandbox._pod.error import ContainerRestartedError, PodError, PodReplacedError
from k8s_sandbox._pod.snapshot import read_pod

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
# Maximum size of a single WebSocket stdin frame. Larger single writes (tens of
# MiB) make the kubelet/API-server/TLS layer reset the connection
# (ConnectionResetError / ssl.SSLEOFError), so stdin is written in chunks.
_STDIN_CHUNK_SIZE = 1024**2  # 1 MiB
# How long a worker blocks in one `WSClient.update()` poll while it is enforcing a
# deadline. Between polls it checks the deadline; the poll itself is still woken
# early by data or by `close_transport()`, so this only bounds how late a deadline
# can be noticed, not how long a live stream waits.
TRANSPORT_POLL_SECONDS = 1.0
# How long one send or receive call on an operation's connection may block. Over
# TLS a send is a whole stdin frame (at most `_STDIN_CHUNK_SIZE`) and must finish
# within this time, so a pod taking its input slower than 1 MiB a minute fails
# too; a receive must get more of the frame it is reading within this time. A
# live pod is far faster than either, so failing them means the pod has stopped
# answering. Waiting for the next frame to start is not bounded here: each
# operation bounds that itself (or, for an exec with no timeout, does not).
TRANSPORT_STALL_SECONDS = 60

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
        # The operation's live transport, so a cancelling caller can wake the
        # worker from another thread. `update(timeout=None)` blocks until the
        # socket has data or is shut down, and a stdin write to a pod that has
        # stopped reading blocks until there is room; shutting the socket down is
        # the only way to end either wait.
        self._transport_lock = threading.Lock()
        self._transport: WSClient | None = None
        # Set by close_transport() under the same lock, so a worker that is still
        # connecting when its caller gives up sees it once its connection attaches,
        # instead of running the operation with nobody waiting for the result.
        self._cancelled = False

    def close_transport(self) -> None:
        """Shut down this operation's socket, waking a blocked worker thread.

        Called from the event loop (not the worker) when the awaiting caller is
        cancelled, so it must not block. That rules out `WSClient.close()`: it
        sends a close frame under the WebSocket's send lock, which a worker
        blocked writing stdin to a pod that stopped reading holds indefinitely,
        and then waits up to 3s for the pod's reply. Shutting the socket down
        takes no lock and returns at once, and it fails a blocked read or write
        immediately, so the worker finishes and performs the full close itself in
        its `finally`. Safe to call at any time, including after the worker has
        closed the transport. Called before the worker has connected, it records
        the cancellation instead, and the worker closes its connection as soon as
        it attaches, without running the operation.
        """
        with self._transport_lock:
            self._cancelled = True
            transport = self._transport
        if transport is not None:
            _shutdown_socket(transport)

    def _write_stdin_chunked(self, ws_client: WSClient, data: str | bytes) -> None:
        """Write ``data`` to the stdin channel in ``_STDIN_CHUNK_SIZE`` frames.

        Used by both exec and write_file (see ``_STDIN_CHUNK_SIZE`` for why we
        chunk). The slice type is preserved: ``str`` -> text frames, ``bytes``
        -> binary frames.
        """
        try:
            for i in range(0, len(data), _STDIN_CHUNK_SIZE):
                ws_client.write_stdin(data[i : i + _STDIN_CHUNK_SIZE])
        except websocket.WebSocketTimeoutException as e:
            raise PodError(
                f"Sending to pod stalled: a write did not complete within "
                f"{TRANSPORT_STALL_SECONDS}s; the pod is not answering.",
                pod=self._pod.name,
            ) from e

    def create_websocket_client_for_exec(
        self, **kwargs
    ) -> Generator[WSClient, None, None]:
        if self._cancelled:
            raise self._cancelled_before_start()
        client = k8s_client(self._pod.context_name)
        # Note: ApiException is intentionally not caught; it should fail the eval.
        ws_client: WSClient = stream(
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
        with self._transport_lock:
            cancelled = self._cancelled
            if not cancelled:
                self._transport = ws_client
        if cancelled:
            # The caller was cancelled while this worker was connecting, so
            # close_transport() found nothing to shut down. Close the connection
            # before anything is sent on it or read from it.
            ws_client.close()
            raise self._cancelled_before_start()
        try:
            # Bound each send and receive on the socket. Without it a stdin write
            # to a pod that has stopped reading blocks for as long as the process
            # lives, and so does a frame that stops arriving partway.
            ws_client.sock.settimeout(TRANSPORT_STALL_SECONDS)
            self._discard_duplicate_channel(ws_client)
            keepalive.start()
            yield ws_client
        except BaseException:
            # The operation failed (a deadline, a stalled send, a lost connection)
            # or was cancelled, so nothing more needs to reach the pod. Shut the
            # socket down before the close below: a pod that has stopped reading
            # leaves the send buffer full, and the close frame would then wait
            # `TRANSPORT_STALL_SECONDS` for room, and up to 3s more for the pod's
            # reply, before the error could reach the caller.
            _shutdown_socket(ws_client)
            raise
        finally:
            with self._transport_lock:
                self._transport = None
            stop_keepalive.set()
            ws_client.close()

    def _cancelled_before_start(self) -> PodError:
        return PodError(
            "Pod operation was cancelled before it started.", pod=self._pod.name
        )

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


def check_for_pod_restart(pod: PodInfo) -> None:
    """Check whether the pod has been replaced or its container has restarted.

    Always raises a typed exception when a change is detected; callers
    (typically ``Pod._check_for_pod_restart_sync``) are responsible for
    applying the ``restarted_container_behavior`` policy and refreshing any
    cached identity.

    Raises:
        PodReplacedError: the pod's UID has changed since ``pod.uid``.
        ContainerRestartedError: the default container's restart count has
            increased since ``pod.initial_restart_count``.
        RuntimeError: the named container is no longer present on the pod
            (treated as a permanent misconfiguration).
    """
    api = k8s_client(pod.context_name)
    snapshot = read_pod(
        api, name=pod.name, namespace=pod.namespace, request_timeout=API_TIMEOUT
    )
    if snapshot.uid != pod.uid:
        # Capture the new pod's restart count for the default container so the
        # caller can refresh its full cached identity atomically.
        raise PodReplacedError(
            pod_name=pod.name,
            old_uid=pod.uid,
            new_uid=snapshot.uid,
            new_restart_count=snapshot.restart_count_for(pod.default_container_name),
        )
    if snapshot.container_statuses is None:
        # Kubelet hasn't published container statuses yet (briefly possible
        # right after pod scheduling). Nothing to compare against — skip the
        # restart-count check.
        return
    status = snapshot.status_for(pod.default_container_name)
    if status is None:
        raise RuntimeError(
            f"Pod '{snapshot.name}' does not have a container named "
            f"'{pod.default_container_name}'"
        )
    if status.restart_count > pod.initial_restart_count:
        raise ContainerRestartedError(
            pod_name=pod.name,
            container_name=status.name,
            restart_count=status.restart_count,
            last_reason=status.last_terminated_reason or "unknown",
        )


def _shutdown_socket(ws_client: WSClient) -> None:
    """Shut down a connection's socket, failing any send or receive blocked on it.

    Takes no lock, sends nothing and returns at once, so it never waits on the pod
    or on another thread holding the WebSocket's send lock.
    """
    # WSClient.sock is the websocket-client WebSocket; its own .sock is the
    # underlying (TLS) socket, or None once the connection has been closed.
    raw_socket = ws_client.sock.sock
    if raw_socket is None:
        return
    try:
        # The base-class call on purpose: `SSLSocket.shutdown()` first drops the TLS
        # layer, and a frame another thread sends in that instant would go out over
        # the plain socket, unencrypted. Shutting down the underlying socket keeps
        # TLS in place, so later sends fail instead.
        socket.socket.shutdown(raw_socket, socket.SHUT_RDWR)
    except OSError:
        # Already closed, so nothing is left blocked on it.
        logger.debug("Transport was already closed.", exc_info=True)


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

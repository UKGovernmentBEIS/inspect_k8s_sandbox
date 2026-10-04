"""Cancelling a pod operation must settle its worker, not abandon it.

A cancelled `await` does not stop the thread running the operation: it is
blocked in `WSClient.update(timeout=None)`, which returns only when the socket
has data or is closed. Left running it holds a slot in the shared pool, keeps
the interpreter from exiting (`ThreadPoolExecutor` joins its threads at exit),
and can still write into a destination its caller has already disposed (a
`read_file` destination, raising `ValueError: write to closed file`).
"""

import asyncio
import socket
import threading
import time
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock, patch

import anyio
import pytest
from kubernetes.stream.ws_client import WSClient  # type: ignore
from websocket import WebSocket

from k8s_sandbox._pod.executor import PodOpExecutor
from k8s_sandbox._pod.write import WriteFileOperation


class _FakeTransport:
    """Stands in for a WSClient blocked on a socket that is not answering."""

    def __init__(self) -> None:
        self.released = threading.Event()
        self.closed = False

    def close(self) -> None:
        self.closed = True
        self.released.set()

    def block_until_released(self, timeout: float) -> bool:
        return self.released.wait(timeout)


@pytest.fixture
def one_worker_executor() -> PodOpExecutor:
    # A single worker makes starvation observable: one abandoned operation is
    # the whole pool.
    return PodOpExecutor(max_pod_ops=1)


async def test_cancelling_an_operation_closes_its_transport_and_settles_the_worker(
    one_worker_executor: PodOpExecutor,
) -> None:
    transport = _FakeTransport()
    finished = threading.Event()

    def operation() -> None:
        transport.block_until_released(timeout=10)
        finished.set()

    task = asyncio.create_task(
        one_worker_executor.queue_operation(operation, on_cancel=transport.close)
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The worker is DONE by the time the caller unwinds: that is what lets the
    # caller dispose the destination it handed the operation.
    assert transport.closed, "the cancelling caller must close the transport"
    assert finished.is_set(), "the worker must settle before cancellation propagates"


async def test_a_cancelled_operation_does_not_starve_the_next_one(
    one_worker_executor: PodOpExecutor,
) -> None:
    transport = _FakeTransport()

    def stuck() -> None:
        transport.block_until_released(timeout=10)

    task = asyncio.create_task(
        one_worker_executor.queue_operation(stuck, on_cancel=transport.close)
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # An unrelated, healthy operation must be able to start immediately.
    ran = threading.Event()
    await asyncio.wait_for(
        one_worker_executor.queue_operation(lambda: ran.set()), timeout=5
    )
    assert ran.is_set()


async def test_a_worker_that_never_wakes_does_not_block_the_caller_forever(
    one_worker_executor: PodOpExecutor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing the transport is not guaranteed to wake every stuck read.

    The settle wait is therefore bounded: a transport that does not respond
    delays cancellation by that bound, never indefinitely.
    """
    monkeypatch.setattr("k8s_sandbox._pod.executor.SETTLE_AFTER_CANCEL_SECONDS", 0.2)
    never_wakes = threading.Event()
    task = asyncio.create_task(
        one_worker_executor.queue_operation(
            lambda: never_wakes.wait(timeout=10), on_cancel=lambda: None
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    never_wakes.set()


async def test_settle_survives_cancellation_from_an_anyio_cancel_scope(
    one_worker_executor: PodOpExecutor,
) -> None:
    """inspect_ai callers cancel via anyio scopes, not bare task.cancel().

    anyio's asyncio backend re-delivers cancellation at every await until the
    scope exits, so an unshielded settle is cancelled instantly and the worker
    is abandoned after all -- exactly what the settle exists to prevent.
    """
    transport = _FakeTransport()
    finished = threading.Event()

    def operation() -> None:
        transport.block_until_released(timeout=10)
        finished.set()

    with anyio.move_on_after(0.1):
        await one_worker_executor.queue_operation(operation, on_cancel=transport.close)

    assert transport.closed, "the cancelling caller must close the transport"
    assert finished.is_set(), "the worker must settle under a cancel scope too"


async def test_cancelling_an_operation_with_no_transport_unwinds_immediately(
    one_worker_executor: PodOpExecutor,
) -> None:
    """No transport means nothing can wake the worker: waiting is a pure tax.

    Such operations (the pod-restart check) also write to no caller-owned
    destination, so there is nothing to settle for; the caller must not pay
    the settle bound (default 30s) for them.
    """
    release = threading.Event()
    task = asyncio.create_task(
        one_worker_executor.queue_operation(lambda: release.wait(timeout=10))
    )
    await asyncio.sleep(0.1)
    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started < 1.0, "no-transport cancel must not settle"
    release.set()


def _start(fn: Callable[[], object]) -> tuple[threading.Thread, list[object]]:
    """Run fn on a daemon thread, recording what it returned or raised."""
    outcome: list[object] = []

    def run() -> None:
        try:
            outcome.append(fn())
        except BaseException as e:  # noqa: BLE001 - the test inspects the exception
            outcome.append(e)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


@pytest.mark.parametrize(
    "file_size",
    [32 * 1024**2, 1],
    ids=["worker-blocked-writing", "worker-blocked-reading"],
)
def test_closing_the_transport_wakes_the_worker_without_blocking_the_caller(
    file_size: int,
) -> None:
    """close_transport() runs on the event loop, so it must return at once.

    A cancelled write to a pod that stopped reading leaves its worker blocked in
    a socket send, holding the WebSocket's send lock; a cancelled operation that
    is waiting on a silent pod leaves its worker blocked in a read, with nobody
    to answer a close handshake. Either way the cancelling caller must not wait
    on the pod: blocking here freezes every coroutine in the process.
    """
    ours, pod_end = socket.socketpair()
    ws = WebSocket()
    ws.sock = ours
    ws.connected = True
    with patch("kubernetes.stream.ws_client.create_websocket", return_value=ws):
        transport = WSClient(MagicMock(), "wss://pod", None, capture_all=False)
    writer = WriteFileOperation(MagicMock())
    try:
        with (
            patch("k8s_sandbox._pod.op.k8s_client"),
            patch("k8s_sandbox._pod.op.stream", return_value=transport),
        ):
            worker, worker_outcome = _start(
                lambda: writer.write_file(b"x" * file_size, Path("/dst"))
            )
            # Let the worker reach the pod and block on it.
            time.sleep(0.3)
            assert worker.is_alive(), "the worker should be blocked on the pod"

            closer, _ = _start(writer.close_transport)
            closer.join(1.0)
            assert not closer.is_alive(), "close_transport() blocked on the pod"

            worker.join(5.0)
            assert not worker.is_alive(), "closing the transport did not wake it"
            assert isinstance(worker_outcome[0], Exception)
    finally:
        # Releases anything still blocked on the pod, whatever the outcome.
        pod_end.close()

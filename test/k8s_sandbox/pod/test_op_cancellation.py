"""Cancelling a pod operation must settle its worker, not abandon it.

A cancelled `await` does not stop the thread running the operation: it is
blocked in `WSClient.update(timeout=None)`, which returns only when the socket
has data or is closed. Left running it holds a slot in the shared pool, keeps
the interpreter from exiting (`ThreadPoolExecutor` joins its threads at exit),
and can still write into a destination its caller has already disposed
(agent-c#19253, where the caller was inspect_swe's Centaur transcript drain).
"""

import asyncio
import threading
import time

import anyio
import pytest

from k8s_sandbox._pod.executor import PodOpExecutor


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

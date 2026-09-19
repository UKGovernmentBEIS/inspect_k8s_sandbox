from __future__ import annotations

import asyncio
import contextlib
import contextvars
import os
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, TypeVar

from inspect_ai.util import concurrency

from k8s_sandbox._logger import log_debug

T = TypeVar("T")

# How long a cancelled operation's worker is given to finish once its transport
# has been closed. Waking it is near-instant when the socket is live; this is
# the ceiling for a transport that does not wake at all.
SETTLE_AFTER_CANCEL_SECONDS = 30.0


class PodOpExecutor:
    """
    A singleton class that manages a thread pool executor for running pod operations.

    This class's API is asynchronous, but the operations it runs are synchronous. It
    runs operations in a thread pool executor.

    Interacts with Inspect's concurrency context manager for the purpose of displaying
    the number of ongoing operations.
    """

    _instance: PodOpExecutor | None = None

    def __init__(self, max_pod_ops: int | None = None) -> None:
        if max_pod_ops is not None:
            self._max_workers = max_pod_ops
            source = "max_pod_ops argument"
        else:
            try:
                self._max_workers = int(os.environ["INSPECT_MAX_POD_OPS"])
                source = "INSPECT_MAX_POD_OPS env var"
            except (KeyError, ValueError):
                cpu_count = os.cpu_count() or 1
                # Pod operations are typically I/O-bound (from the
                # client's perspective).
                self._max_workers = cpu_count * 4
                source = f"default (cpu_count={cpu_count} * 4)"
        log_debug(
            "Creating PodOpExecutor.",
            max_workers=self._max_workers,
            source=source,
        )
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="pod-op-executor"
        )

    @classmethod
    def get_instance(cls, max_pod_ops: int | None = None) -> PodOpExecutor:
        """Gets the singleton instance of the PodOpExecutor.

        Args:
            max_pod_ops: Maximum number of concurrent pod operations. If provided
                on the first call, overrides the INSPECT_MAX_POD_OPS env var and
                the default (cpu_count * 4). A later call with a different value
                raises ValueError rather than silently ignoring the configuration.

        This method is async-safe (because it doesn't await anything) but not
        thread-safe.
        """
        if cls._instance is None:
            cls._instance = cls(max_pod_ops=max_pod_ops)
        elif max_pod_ops is not None and cls._instance._max_workers != max_pod_ops:
            raise ValueError(
                "PodOpExecutor is already initialized with "
                f"max_pod_ops={cls._instance._max_workers}; cannot use "
                f"max_pod_ops={max_pod_ops}."
            )
        return cls._instance

    async def queue_operation(
        self, callable: Callable[[], T], on_cancel: Callable[[], None] | None = None
    ) -> T:
        """
        Queue a synchronous pod operation to run asynchronously and return the result.

        A thread pool executor is used to run the operation in another thread.

        Inspect's concurrency context manager is used so that the user gets visibility
        of the number of ongoing operations. Other than the user display, the
        use of the semaphore is redundant.

        This method is async-safe but not thread-safe.
        """
        async with concurrency("pod-op", self._max_workers):
            # run_in_executor does not propagate the caller's context into the
            # worker thread, so pass it directly to preserve Inspect
            # sandbox config overrides
            context = contextvars.copy_context()
            worker = self._executor.submit(lambda: context.run(callable))
            try:
                return await asyncio.wrap_future(worker)
            except asyncio.CancelledError:
                # Cancelling the await does not stop the worker: it is a thread
                # blocked on a socket read that may never return. Left alone it
                # holds a pool slot the next operation needs, keeps the process
                # from exiting (ThreadPoolExecutor joins its threads at
                # interpreter exit), and may still write into a destination the
                # caller is about to dispose. So wake it by closing its
                # transport, then wait for it to finish before unwinding.
                if on_cancel is not None:
                    on_cancel()
                await self._settle(worker)
                raise

    @staticmethod
    async def _settle(worker: Future[T]) -> None:
        """Wait for a cancelled operation's worker to finish, within a bound."""
        with contextlib.suppress(asyncio.TimeoutError, Exception):
            await asyncio.wait_for(
                asyncio.wrap_future(worker), SETTLE_AFTER_CANCEL_SECONDS
            )
        if not worker.done():
            # The transport did not wake it. Nothing further is available from
            # here, and blocking the event loop would spread one stuck socket
            # to the whole eval; say so where an operator can see it.
            log_debug(
                "Pod operation did not settle after its transport was closed; "
                "its worker thread is still running.",
                settle_timeout=SETTLE_AFTER_CANCEL_SECONDS,
            )

from __future__ import annotations

import asyncio
import contextvars
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

from inspect_ai.util import concurrency

from k8s_sandbox._logger import log_debug

T = TypeVar("T")


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
            self._source = "max_pod_ops argument"
        else:
            try:
                self._max_workers = int(os.environ["INSPECT_MAX_POD_OPS"])
                self._source = "INSPECT_MAX_POD_OPS env var"
            except (KeyError, ValueError):
                cpu_count = os.cpu_count() or 1
                # Pod operations are typically I/O-bound (from the
                # client's perspective).
                self._max_workers = cpu_count * 4
                self._source = f"default (cpu_count={cpu_count} * 4)"
        log_debug(
            "Creating PodOpExecutor.",
            max_workers=self._max_workers,
            source=self._source,
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
                f"max_pod_ops={cls._instance._max_workers} (from "
                f"{cls._instance._source}); cannot use max_pod_ops={max_pod_ops}. "
                "The limit is process-wide: set the same max_pod_ops on every "
                "task, or use the INSPECT_MAX_POD_OPS env var."
            )
        return cls._instance

    async def queue_operation(self, callable: Callable[[], T]) -> T:
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
            return await asyncio.get_event_loop().run_in_executor(
                self._executor, lambda: context.run(callable)
            )

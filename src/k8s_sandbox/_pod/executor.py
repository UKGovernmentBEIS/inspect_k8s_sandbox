from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

from inspect_ai.util import concurrency

from aisitools.k8s_sandbox._logger import sandbox_log

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

    def __init__(self) -> None:
        try:
            self._max_workers = int(os.environ["INSPECT_MAX_POD_OPS"])
        except (KeyError, ValueError):
            cpu_count = os.cpu_count() or 1
            # Pod operations are typically I/O-bound (from the client's perspective).
            self._max_workers = cpu_count * 4
        sandbox_log("Creating PodOpExecutor.", max_workers=self._max_workers)
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="pod-op-executor"
        )

    @classmethod
    def get_instance(cls) -> PodOpExecutor:
        """Gets the singleton instance of the PodOpExecutor.

        This method is async-safe (because it doesn't await anything) but not
        thread-safe.
        """
        if cls._instance is None:
            cls._instance = cls()
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
            return await asyncio.get_event_loop().run_in_executor(
                self._executor, callable
            )

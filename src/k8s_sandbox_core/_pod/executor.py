from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

from k8s_sandbox_core._concurrency import concurrency
from k8s_sandbox_core._logger import log_debug

T = TypeVar("T")


class PodOpExecutor:
    """
    A singleton class that manages a thread pool executor for running pod operations.

    This class's API is asynchronous, but the operations it runs are synchronous. It
    runs operations in a thread pool executor.
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
        """Gets the singleton instance of the PodOpExecutor."""
        if cls._instance is None:
            cls._instance = cls(max_pod_ops=max_pod_ops)
        return cls._instance

    async def queue_operation(self, callable: Callable[[], T]) -> T:
        """Queue a synchronous pod operation to run asynchronously."""
        async with concurrency("pod-op", self._max_workers):
            return await asyncio.get_event_loop().run_in_executor(
                self._executor, callable
            )

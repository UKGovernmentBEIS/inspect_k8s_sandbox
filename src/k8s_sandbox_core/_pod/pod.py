from __future__ import annotations

from pathlib import Path
from typing import IO, Callable, Literal, TypeVar

from k8s_sandbox_core._exec_result import ExecResult

from k8s_sandbox_core._pod.execute import ExecuteOperation
from k8s_sandbox_core._pod.executor import PodOpExecutor
from k8s_sandbox_core._pod.op import PodInfo, check_for_pod_restart
from k8s_sandbox_core._pod.read import ReadFileOperation
from k8s_sandbox_core._pod.write import WriteFileOperation

T = TypeVar("T")


class Pod:
    def __init__(
        self,
        name: str,
        namespace: str,
        context_name: str | None,
        default_container_name: str,
        uid: str,
        initial_restart_count: int,
        restarted_container_behavior: Literal["warn", "raise"],
    ) -> None:
        self.info = PodInfo(
            name,
            namespace,
            context_name,
            default_container_name,
            uid,
            initial_restart_count,
            restarted_container_behavior,
        )

    async def check_for_pod_restart(self) -> None:
        """Check if the pod has been replaced or its container has restarted."""
        await self._run_async(lambda: check_for_pod_restart(self.info))

    async def exec(
        self,
        cmd: list[str],
        stdin: str | bytes | None,
        cwd: str | None,
        env: dict[str, str],
        user: str | None,
        timeout: int | None,
    ) -> ExecResult[str]:
        """Execute a command in a pod."""
        executor = ExecuteOperation(self.info)
        result = await self._run_async(
            lambda: executor.exec(cmd, stdin, cwd, env, user, timeout)
        )
        return result

    async def write_file(self, src: IO[bytes], dst: Path) -> None:
        """Copy a file-like object (src) from the client to a path on the pod (dst)."""
        writer = WriteFileOperation(self.info)
        await self._run_async(lambda: writer.write_file(src, dst))

    async def read_file(self, src: Path, dst: IO[bytes]) -> None:
        """Copy a file from the pod (src) to a file-like object (dst) on the client."""
        reader = ReadFileOperation(self.info)
        await self._run_async(lambda: reader.read_file(src, dst))

    async def _run_async(self, callable: Callable[[], T]) -> T:
        """Run a synchronous function asynchronously."""
        executor = PodOpExecutor.get_instance()
        return await executor.queue_operation(callable)

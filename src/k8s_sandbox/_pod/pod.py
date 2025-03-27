from __future__ import annotations

from pathlib import Path
from typing import IO, Callable, TypeVar

from inspect_ai.util import ExecResult

from k8s_sandbox._pod.execute import ExecuteOperation
from k8s_sandbox._pod.executor import PodOpExecutor
from k8s_sandbox._pod.op import PodInfo
from k8s_sandbox._pod.read import ReadFileOperation
from k8s_sandbox._pod.write import WriteFileOperation

T = TypeVar("T")


class Pod:
    def __init__(
        self,
        name: str,
        namespace: str,
        context_name: str | None,
        default_container_name: str,
    ) -> None:
        self.info = PodInfo(name, namespace, context_name, default_container_name)

    async def exec(
        self,
        cmd: list[str],
        stdin: str | bytes | None,
        cwd: str | None,
        env: dict[str, str],
        timeout: int | None,
    ) -> ExecResult[str]:
        """
        Execute a command in a pod.

        This method will return when and only when the supplied command exits, even if
        the command has launched background processes (e.g. with `bash -c "foo &"`).
        Any background processes will continue to run and will not be subject to the
        optional timeout.

        When executing a command over connect_get_namespaced_pod_exec, the websocket
        connection is not "naturally" closed until both:
        - The command has exited.
        - The stdout and stderr streams have been closed (including by any commands
          which have inherited them).
        This is behaviour of the CRI-O implementation which is running on the Kubernetes
        nodes.

        To support the required functionality, the supplied command is executed in a
        shell (/bin/sh).

        To allow this method to return when the supplied command has completed, even if
        backgrounded processes which inherit stdout or stderr are still running, a
        sentinel value is written to stdout after the supplied command has completed.
        When this sentinel value is detected, we close the websocket connection. This
        sentinel value also includes the exit code of the supplied command, as we won't
        have access to /bin/sh's return code if we manually close the websocket.

        Args:
          cmd (list[str]): The command and arguments to execute.
          stdin (str | bytes | None): The optional standard input to pipe into cmd.
            The stdin file descriptor will be closed after the input has been written.
          cwd (str | None): The working directory to change to before executing cmd.
            Relative directories will be resolved relative to the pod's default working
            directory. If None, the default working directory is used. If the provided
            directory does not exist, an unsuccessful ExecResult will be returned and
            cmd will not be run.
          env (dict[str, str]): The environment variables to set before running cmd.
          timeout (int | None): The optional timeout for cmd to complete in. Defaults to
            no timeout. If provided, SIGTERM will be sent to cmd once the timeout has
            elapsed. This is enforced by the `timeout` command on the pod. This will not
            terminate background processes started by cmd.
        """
        executor = ExecuteOperation(self.info)
        result = await self._run_async(
            lambda: executor.exec(cmd, stdin, cwd, env, timeout)
        )
        return result

    async def write_file(self, src: IO[bytes], dst: Path) -> None:
        """
        Copy a file-like object (src) from the client to a path on the pod (dst).

        Existing files on the pod will be overwritten.

        The source will be read from its current position to the end of the file. The
        file position will be restored after the copy. The file-like object must be
        opened in binary mode.

        Args:
          src (IO[bytes]): The file-like object which contains the contents to be
            written to the pod.
          dst (Path): The path to write the file to on the pod. Relative paths will be
            resolved relative to the pod's default working directory.
        """
        writer = WriteFileOperation(self.info)
        await self._run_async(lambda: writer.write_file(src, dst))

    async def read_file(self, src: Path, dst: IO[bytes]) -> None:
        """
        Copy a file from the pod (src) to the a file-like object (dst) on the client.

        The file-like object will not be seeked before or after the read. The file-like
        object must be opened for writing in binary mode.

        Args:
          src (Path): The path to the file on the pod. Relative paths will be resolved
            relative to the pod's default working directory.
          dst (IO[bytes]): A file-like object to write the file to on the client system.
        """
        reader = ReadFileOperation(self.info)
        await self._run_async(lambda: reader.read_file(src, dst))

    async def _run_async(self, callable: Callable[[], T]) -> T:
        """Run a synchronous function asynchronously."""
        executor = PodOpExecutor.get_instance()
        return await executor.queue_operation(callable)

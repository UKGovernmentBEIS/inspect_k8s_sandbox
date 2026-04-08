import base64
import re
import shlex
from contextlib import contextmanager
from typing import Generator

from k8s_sandbox_core._exec_result import ExecResult
from k8s_sandbox_core._limits import OutputLimitExceededError
from k8s_sandbox_core._limits import SandboxLimits as limits
from kubernetes.stream.ws_client import WSClient  # type: ignore[import-untyped]

from k8s_sandbox_core._pod.buffer import LimitedBuffer
from k8s_sandbox_core._pod.error import ExecutableNotFoundError, PodError
from k8s_sandbox_core._pod.get_returncode import get_returncode
from k8s_sandbox_core._pod.op import PodOperation

COMPLETED_SENTINEL = "completed-sentinel-value"
COMPLETED_SENTINEL_PATTERN = re.compile(rf"<{COMPLETED_SENTINEL}-(\d+)>")
EXEC_USER_URL = "https://k8s-sandbox.aisi.org.uk/design/limitations#exec-user"


class ExecuteOperation(PodOperation):
    def exec(
        self,
        cmd: list[str],
        stdin: str | bytes | None,
        cwd: str | None,
        env: dict[str, str],
        user: str | None,
        timeout: int | None,
    ) -> ExecResult[str]:
        shell_script = self._build_shell_script(cmd, stdin, cwd, env, timeout)
        with self._interactive_shell(user) as ws_client:
            # Write the script to the shell's stdin rather than passing it as a command
            # argument (-c) to better support potentially long commands.
            ws_client.write_stdin(shell_script)
            result = self._handle_shell_output(ws_client, user, timeout)
        return result

    @contextmanager
    def _interactive_shell(self, user: str | None) -> Generator[WSClient, None, None]:
        command = ["/bin/sh"]
        if user is not None:
            command = ["runuser", "-u", user] + command
        try:
            yield from self.create_websocket_client_for_exec(
                command=command,
                stderr=True,
                stdin=True,
                stdout=True,
                # Leave stdout and stderr as binary. Has no effect on stdin.
                binary=True,
            )
        # Raised if /bin/sh or runuser cannot be found in the Pod (not if a
        # user-supplied) command cannot be found.
        except ExecutableNotFoundError as e:
            if 'error finding executable "runuser"' in str(e):
                raise RuntimeError(
                    f"When a user parameter ('{user}') is provided to exec(), the "
                    f"runuser binary must be installed in the container. Docs: "
                    f"{EXEC_USER_URL}"
                ) from e
            raise

    def _build_shell_script(
        self,
        command: list[str],
        stdin: str | bytes | None,
        cwd: str | None,
        env: dict[str, str],
        timeout: int | None,
    ) -> str:
        def generate() -> Generator[str, None, None]:
            if cwd is not None:
                yield f"cd {shlex.quote(cwd)} || exit $?\n"
            for key, value in env.items():
                yield f"export {shlex.quote(key)}={shlex.quote(value)}\n"
            if stdin is not None:
                yield self._pipe_user_input(stdin)
            yield f"{self._prefix_timeout(timeout)}{shlex.join(command)}\n"
            # Store the returncode so that the `echo` below doesn't overwrite it.
            yield "returncode=$?\n"
            # Ensure stdout and stderr are flushed before writing the sentinel value.
            yield "sync\n"
            # Write a sentinel value to stdout to determine when the user command
            # has completed. Also write the returncode as we won't have access to it if
            # we manually close the websocket connection.
            yield f'echo -n "<{COMPLETED_SENTINEL}-$returncode>"\n'
            # Exit the shell. This won't actually close the websocket connection until
            # stdout and stderr (which have been inherited by the user command) are
            # closed. But it will force the echo above to be flushed.
            yield "exit $returncode\n"

        return "".join(generate())

    def _pipe_user_input(self, stdin: str | bytes) -> str:
        stdin_b64 = base64.b64encode(
            stdin if isinstance(stdin, bytes) else stdin.encode("utf-8")
        ).decode("ascii")
        return f"echo '{stdin_b64}' | base64 -d | "

    def _prefix_timeout(self, timeout: int | None) -> str:
        if timeout is None:
            return ""
        return f"timeout -k 5s {timeout}s "

    def _handle_shell_output(
        self, ws_client: WSClient, user: str | None, timeout: int | None
    ) -> ExecResult[str]:
        def stream_output() -> ExecResult[str]:
            stdout = LimitedBuffer(limits.MAX_EXEC_OUTPUT_SIZE)
            stderr = LimitedBuffer(limits.MAX_EXEC_OUTPUT_SIZE)
            returncode: int | None = None
            while ws_client.is_open():
                try:
                    ws_client.update(timeout=None)
                    if ws_client.peek_stderr():
                        stderr.append(ws_client.read_stderr())
                    if ws_client.peek_stdout():
                        frame = ws_client.read_stdout()
                        filtered, returncode = self._filter_sentinel_and_returncode(
                            frame
                        )
                        stdout.append(filtered)
                        if returncode is not None:
                            ws_client.close()
                    self._verify_output_limit(stdout, stderr)
                except (BrokenPipeError, ConnectionResetError) as e:
                    if returncode is not None:
                        break
                    raise PodError(
                        "WebSocket connection lost during exec",
                        pod=self._pod.name,
                    ) from e
            if returncode is None:
                returncode = get_returncode(ws_client)
            return ExecResult(
                success=returncode == 0,
                returncode=returncode,
                stdout=str(stdout),
                stderr=str(stderr),
            )

        result = stream_output()
        if timeout is not None and result.returncode == 124:
            raise TimeoutError(f"Command timed out after {timeout}s. {result}")
        if result.returncode == 126 and "permission denied" in result.stderr.casefold():
            raise PermissionError(f"Permission denied executing command. {result}")
        if result.returncode != 0 and user is not None:
            self._check_for_runuser_error(result.stderr, user)
        return result

    def _check_for_runuser_error(self, stderr: str, user: str) -> None:
        if re.search(r"runuser: user \S+ does not exist", stderr, re.IGNORECASE):
            raise RuntimeError(
                f"The user parameter '{user}' provided to exec() does "
                f"not appear to exist in the container. Docs: {EXEC_USER_URL}\n{stderr}"
            )
        if "runuser: may not be used by non-root users" in stderr.casefold():
            raise RuntimeError(
                f"When a user parameter ('{user}') is provided to exec(), the "
                f"container must be running as root. Docs: {EXEC_USER_URL}\n{stderr}"
            )

    def _filter_sentinel_and_returncode(self, frame: bytes) -> tuple[bytes, int | None]:
        decoded = frame.decode("utf-8", errors="strict")
        split_frame = re.split(COMPLETED_SENTINEL_PATTERN, decoded)
        if len(split_frame) == 1:
            return frame, None
        filtered = split_frame[0] + split_frame[2]
        return filtered.encode("utf-8"), int(split_frame[1])

    def _verify_output_limit(
        self, stdout: LimitedBuffer, stderr: LimitedBuffer
    ) -> None:
        if stdout.truncated or stderr.truncated:
            raise OutputLimitExceededError(
                limit_str=limits.MAX_EXEC_OUTPUT_SIZE_STR,
                truncated_output=str(stdout) + str(stderr),
            )

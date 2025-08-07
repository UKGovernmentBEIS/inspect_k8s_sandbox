import base64
import re
import shlex
from contextlib import contextmanager
from typing import Generator

from inspect_ai.util import ExecResult, OutputLimitExceededError
from inspect_ai.util import SandboxEnvironmentLimits as limits
from kubernetes.stream.ws_client import WSClient  # type: ignore

from k8s_sandbox._pod.buffer import LimitedBuffer
from k8s_sandbox._pod.error import ExecutableNotFoundError
from k8s_sandbox._pod.get_returncode import get_returncode
from k8s_sandbox._pod.op import PodOperation

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
        self._check_for_pod_restart()
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
        # Encode the user-provided input as base64 for 2 reasons:
        # 1. To avoid issues with special characters (e.g. new lines) in the input.
        # 2. To support binary input (e.g. null byte).
        stdin_b64 = base64.b64encode(
            stdin if isinstance(stdin, bytes) else stdin.encode("utf-8")
        ).decode("ascii")
        # Pipe user input. Simply writing it to the shell's stdin after a command e.g.
        # `cat` results in `cat` blocking indefinitely as there is no way to close the
        # stdin stream in v4.channel.k8s.io.
        return f"echo '{stdin_b64}' | base64 -d | "

    def _prefix_timeout(self, timeout: int | None) -> str:
        if timeout is None:
            return ""
        # Enforce timeout using `timeout` on the Pod. Simpler than alternative of
        # enforcing this on the client side (requires terminating the remote process).
        # `-k 5s` sends SIGKILL after grace period in case user command doesn't respect
        # SIGTERM.
        return f"timeout -k 5s {timeout}s "

    def _handle_shell_output(
        self, ws_client: WSClient, user: str | None, timeout: int | None
    ) -> ExecResult[str]:
        def stream_output() -> ExecResult[str]:
            stdout = LimitedBuffer(limits.MAX_EXEC_OUTPUT_SIZE)
            stderr = LimitedBuffer(limits.MAX_EXEC_OUTPUT_SIZE)
            returncode: int | None = None
            while ws_client.is_open():
                # `timeout=None` means `update` will block indefinitely until there is
                # data to read from the socket.
                ws_client.update(timeout=None)
                # Note: `peek_*()` and `read_*()` may call `update(timeout=0)`.
                if ws_client.peek_stderr():
                    stderr.append(ws_client.read_stderr())
                # Handle stdout _after_ stderr to guarantee that, if buffered, the
                # sentinel is actioned before the blocking `ws_client.update(None)`.
                if ws_client.peek_stdout():
                    frame = ws_client.read_stdout()
                    # Assumption: The sentinel value is written to stdout in a single
                    # frame and not split by other writes to stdout.
                    filtered, returncode = self._filter_sentinel_and_returncode(frame)
                    stdout.append(filtered)
                    if returncode is not None:
                        ws_client.close()
                self._verify_output_limit(stdout, stderr)
            # returncode won't be set if setup commands e.g. `cd` failed.
            if returncode is None:
                returncode = get_returncode(ws_client)
            return ExecResult(
                success=returncode == 0,
                returncode=returncode,
                stdout=str(stdout),
                stderr=str(stderr),
            )

        result = stream_output()
        # 124 is the exit code for the `timeout` command.
        if timeout is not None and result.returncode == 124:
            raise TimeoutError(f"Command timed out after {timeout}s. {result}")
        # The Inspect SandboxEnvironment interface expects us to raise a
        # PermissionError for exit code 126 and stderr containing "permission denied".
        if result.returncode == 126 and "permission denied" in result.stderr.casefold():
            raise PermissionError(f"Permission denied executing command. {result}")
        # Only parse runuser errors if the user parameter was set. Don't raise errors if
        # the user-supplied command happened to use `runuser` itself.
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
        # We don't support returning binary data from an exec() command and are expected
        # to raise a UnicodeDecodeError if we encounter one, so errors="strict".
        # Assumption: individual frames are valid utf-8 (i.e. characters are not split
        # across frames).
        decoded = frame.decode("utf-8", errors="strict")
        split_frame = re.split(COMPLETED_SENTINEL_PATTERN, decoded)
        if len(split_frame) == 1:
            return frame, None
        # Remove the sentinel value from the stdout frame.
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

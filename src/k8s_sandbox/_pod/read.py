import shlex
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Generator

from inspect_ai.util import OutputLimitExceededError
from inspect_ai.util import SandboxEnvironmentLimits as limits
from kubernetes.stream.ws_client import (  # type: ignore[import-untyped]
    ERROR_CHANNEL,
    STDERR_CHANNEL,
    STDOUT_CHANNEL,
    WSClient,
)

from k8s_sandbox._pod.buffer import LimitedBuffer
from k8s_sandbox._pod.error import PodError
from k8s_sandbox._pod.get_returncode import parse_returncode
from k8s_sandbox._pod.op import (
    PodOperation,
    raise_for_known_read_write_errors,
)
from k8s_sandbox._pod.stream import iter_channel_frames

COMPLETE_SENTINEL = b"<read-file-complete-sentinel>"


class ReadFileOperation(PodOperation):
    def read_file(self, src: Path, dst: IO[bytes]) -> None:
        with self._start_read_command(src) as ws_client:
            self._handle_stream_output(ws_client, dst)

    @contextmanager
    def _start_read_command(self, src: Path) -> Generator[WSClient, None, None]:
        # Limit number of bytes read (-c) to 1 byte over the limit (to detect if the
        # file is too large). The filename is passed as an argument rather than
        # interpolated, so that no filename can be read as shell syntax.
        head = f'head -c {limits.MAX_READ_FILE_SIZE + 1} "$1"'
        # A truncated response is otherwise indistinguishable from a complete one: the
        # stream can end part-way through the file with `{"status": "Success"}` on the
        # status channel (kubernetes/kubernetes#142376, open). That issue closing is not
        # reason enough to drop this: the fix reaches a cluster only when the cluster is
        # upgraded. The sentinel is the last thing written, so its absence means we did
        # not receive the whole file. `&&` leaves a failing `head`'s exit code and
        # stderr as the caller's, unsentinelled.
        # Don't make the command wait on stdin for the client to confirm receipt: the
        # stdin write doesn't reach a pod whose client has been reading slowly (the
        # only case it would help), and it strands a shell blocked on `read`. See #278.
        sentinel = f"printf %s {shlex.quote(COMPLETE_SENTINEL.decode())}"
        command = ["/bin/sh", "-c", f"{head} && {sentinel}", "sh", src.as_posix()]
        yield from self.create_websocket_client_for_exec(
            command=command,
            stderr=True,
            # Without stdin, kubelet closes the exec's input as soon as the stream
            # opens, which races the stdout read in Kata Containers' Go shim and hangs
            # or truncates the read (kata-containers/kata-containers#9071).
            stdin=True,
            stdout=True,
            # Leave stdout (and stderr) as binary.
            binary=True,
        )

    def _handle_stream_output(self, ws_client: WSClient, dst: IO[bytes]) -> None:
        # `head` should not produce large amounts of stderr, but limit it nonetheless.
        stderr = LimitedBuffer(limits.MAX_EXEC_OUTPUT_SIZE)
        status = b""
        start_position = dst.tell()
        # The sentinel is not part of the file, and it can arrive split across
        # frames, so always hold back the last len(COMPLETE_SENTINEL) bytes seen.
        pending = b""
        # Stream the response, writing it to dst as we go to avoid holding the whole
        # response in memory.
        for channel, payload in iter_channel_frames(ws_client):
            if channel == STDOUT_CHANNEL:
                pending += payload
                if len(pending) > len(COMPLETE_SENTINEL):
                    dst.write(pending[: -len(COMPLETE_SENTINEL)])
                    pending = pending[-len(COMPLETE_SENTINEL) :]
                    self._verify_output_limit(dst.tell() - start_position)
            elif channel == STDERR_CHANNEL:
                stderr.append(payload)
            elif channel == ERROR_CHANNEL:
                status += payload
        returncode = parse_returncode(status)
        if returncode != 0:
            stderr_str = str(stderr)
            raise_for_known_read_write_errors(stderr_str)
            raise PodError(
                "Unrecognised error reading file from pod.",
                returncode=returncode,
                stderr=stderr_str,
            )
        if pending != COMPLETE_SENTINEL:
            raise PodError(
                "The file read from the pod was incomplete. The exec stream ended "
                "before the whole file had been received.",
                returncode=returncode,
                stderr=str(stderr),
            )
        dst.flush()

    def _verify_output_limit(self, bytes_written: int) -> None:
        if bytes_written > limits.MAX_READ_FILE_SIZE:
            raise OutputLimitExceededError(
                limit_str=limits.MAX_READ_FILE_SIZE_STR, truncated_output=None
            )

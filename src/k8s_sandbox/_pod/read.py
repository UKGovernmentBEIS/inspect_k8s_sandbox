import time
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Generator

from inspect_ai.util import OutputLimitExceededError
from inspect_ai.util import SandboxEnvironmentLimits as limits
from kubernetes.stream.ws_client import WSClient  # type: ignore[import-untyped]

import k8s_sandbox._pod.op as op_module
from k8s_sandbox._pod.buffer import LimitedBuffer
from k8s_sandbox._pod.error import PodError
from k8s_sandbox._pod.get_returncode import get_returncode
from k8s_sandbox._pod.op import (
    PodOperation,
    raise_for_known_read_write_errors,
)

# `head` on a live pod streams the file without pausing, so a read that delivers
# nothing for this long is a pod (or stream) that has gone away, not a slow file.
# Large files stay readable at any speed because every delivered frame resets it.
READ_STALL_SECONDS = 60


class ReadFileOperation(PodOperation):
    def read_file(self, src: Path, dst: IO[bytes]) -> None:
        with self._start_read_command(src) as ws_client:
            self._handle_stream_output(ws_client, dst)

    @contextmanager
    def _start_read_command(self, src: Path) -> Generator[WSClient, None, None]:
        # Limit number of bytes read (-c) to 1 byte over the limit (to detect if the
        # file is too large).
        command = ["head", "-c", limits.MAX_READ_FILE_SIZE + 1, src.as_posix()]
        yield from self.create_websocket_client_for_exec(
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            # Leave stdout (and stderr) as binary.
            binary=True,
        )

    def _handle_stream_output(self, ws_client: WSClient, dst: IO[bytes]) -> None:
        # `head` should not produce large amounts of stderr, but limit it nonetheless.
        stderr = LimitedBuffer(limits.MAX_EXEC_OUTPUT_SIZE)
        start_position = dst.tell()
        # Stream the response, writing it to dst as we go to avoid holding the whole
        # response in memory.
        last_progress = time.monotonic()
        while ws_client.is_open():
            ws_client.update(timeout=op_module.TRANSPORT_POLL_SECONDS)
            progressed = False
            if ws_client.peek_stdout():
                dst.write(ws_client.read_stdout())
                self._verify_output_limit(dst.tell() - start_position)
                progressed = True
            if ws_client.peek_stderr():
                stderr.append(ws_client.read_stderr())
                progressed = True
            if progressed:
                last_progress = time.monotonic()
            elif time.monotonic() - last_progress > READ_STALL_SECONDS:
                raise PodError(
                    f"Reading file from pod stalled: no data for "
                    f"{READ_STALL_SECONDS}s; the pod is not answering.",
                    pod=self._pod.name,
                )
        returncode = get_returncode(ws_client)
        if returncode != 0:
            stderr_str = str(stderr)
            raise_for_known_read_write_errors(stderr_str)
            raise PodError(
                "Unrecognised error reading file from pod.",
                returncode=returncode,
                stderr=stderr_str,
            )
        dst.flush()

    def _verify_output_limit(self, bytes_written: int) -> None:
        if bytes_written > limits.MAX_READ_FILE_SIZE:
            raise OutputLimitExceededError(
                limit_str=limits.MAX_READ_FILE_SIZE_STR, truncated_output=None
            )

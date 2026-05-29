import shlex
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from kubernetes.stream.ws_client import WSClient  # type: ignore[import-untyped]

from k8s_sandbox._pod.error import PodError
from k8s_sandbox._pod.get_returncode import get_returncode
from k8s_sandbox._pod.op import (
    PodOperation,
    raise_for_known_read_write_errors,
)


class WriteFileOperation(PodOperation):
    def write_file(self, data: bytes, dst: Path) -> None:
        with self._start_write_command(dst, len(data)) as ws_client:
            self._write_stdin_chunked(ws_client, data)
            self._handle_stream_output(ws_client)

    @contextmanager
    def _start_write_command(
        self, dst: Path, file_size: int
    ) -> Generator[WSClient, None, None]:
        mkdir_command = f"mkdir -p {shlex.quote(dst.parent.as_posix())}"
        # Use `head` with `-c <file size>` because we have no way of closing the stdin
        # stream in v4.channel.k8s.io (which means the websocket connection would never
        # close).
        head_command = f"head -c {file_size}"
        command = [
            "/bin/sh",
            "-c",
            f"{mkdir_command} && {head_command} > {shlex.quote(dst.as_posix())}",
        ]
        yield from self.create_websocket_client_for_exec(
            command=command,
            stderr=True,
            stdin=True,
            stdout=True,
            # Read stdout and stderr as text. Has no effect on stdin.
            binary=False,
        )

    def _handle_stream_output(self, ws_client: WSClient) -> None:
        # Wait until the websocket connection is closed. All stderr will be stored by us
        # in memory anyway so there is no value in streaming it.
        ws_client.run_forever()
        returncode = get_returncode(ws_client)
        if returncode != 0:
            stderr = ws_client.read_stderr()
            raise_for_known_read_write_errors(stderr)
            raise PodError(
                "Unrecognised error writing file to pod.",
                returncode=returncode,
                stderr=stderr,
            )

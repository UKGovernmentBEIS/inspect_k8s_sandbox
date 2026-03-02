import io
import shlex
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Generator

from kubernetes.stream.ws_client import WSClient  # type: ignore[import-untyped]

from k8s_sandbox._pod.error import PodError
from k8s_sandbox._pod.get_returncode import get_returncode
from k8s_sandbox._pod.op import (
    PodOperation,
    raise_for_known_read_write_errors,
)


class WriteFileOperation(PodOperation):
    def write_file(self, src: IO[bytes], dst: Path) -> None:
        self._check_for_pod_restart()
        file_size = self._get_file_size(src)
        with self._start_write_command(dst, file_size) as ws_client:
            self._write_data_to_stdin(ws_client, src)
            self._handle_stream_output(ws_client)

    def _get_file_size(self, file: IO[bytes]) -> int:
        original_position = file.tell()
        file.seek(0, io.SEEK_END)
        file_size = file.tell()
        file.seek(original_position)
        return file_size

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

    def _write_data_to_stdin(self, ws_client: WSClient, src: IO[bytes]) -> None:
        original_position = src.tell()
        # Write the src in chunks of 1MiB as large writes (~100MiB) result in
        # ssl.SSLEOFError.
        chunk_size = 1024**2  # 1 MiB
        while data := src.read(chunk_size):
            ws_client.write_stdin(data)
        src.seek(original_position)

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

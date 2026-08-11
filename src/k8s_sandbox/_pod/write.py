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
        dst_quoted = shlex.quote(dst.as_posix())
        # Do not remove `exec 3>&1`. It holds the exec stdout pipe open for the lifetime
        # of the command. Otherwise, in shells which exec into the final command (e.g.
        # busybox ash, but not dash), nothing holds that pipe once `head`'s stdout is
        # redirected to the file. It then EOFs immediately, containerd responds by
        # closing stdin, and `head -c N` exits 0 on the short read, silently truncating.
        # https://github.com/UKGovernmentBEIS/inspect_k8s_sandbox/issues/225
        command = [
            "/bin/sh",
            "-c",
            f"exec 3>&1; {mkdir_command} && {head_command} > {dst_quoted}",
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

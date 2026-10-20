from typing import Iterator

from kubernetes.stream.ws_client import WSClient  # type: ignore[import-untyped]
from websocket import ABNF  # type: ignore[import-untyped]


def iter_channel_frames(ws_client: WSClient) -> Iterator[tuple[int, bytes]]:
    """Yield `(channel, payload)` for each data frame until the server closes.

    This reads the exec stream's frames directly rather than going through
    `WSClient.update()`, which on kubernetes 37 consumes every immediately-
    available frame into a single `bytes` that it grows with `+=`. The copying
    is quadratic in the size of the channel's output: a 100 MiB read took ~80s
    against the ~1s the socket needs.

    Being slow is not merely slow here. A client which reads more slowly than
    the pod writes loses the tail of the output, and the status channel still
    reports `{"status": "Success"}` (kubernetes/kubernetes#142376). Streaming
    the frames out as they arrive keeps the read linear and bounded in memory,
    which keeps us out of the range where that happens.

    Marks `ws_client` closed once the server's close frame arrives, so callers
    can rely on `is_open()` afterwards. A caller which stops early (e.g. on a
    sentinel) should close the client itself.
    """
    while True:
        # `control_frame=False` leaves websocket-client to answer pings and to
        # reassemble continuation frames.
        op_code, frame = ws_client.sock.recv_data_frame(False)
        if op_code == ABNF.OPCODE_CLOSE:
            ws_client.close()
            return
        if op_code not in (ABNF.OPCODE_BINARY, ABNF.OPCODE_TEXT):
            continue
        data: bytes = frame.data
        # The first byte of each frame is the channel; an empty payload (a bare
        # channel byte) carries nothing. The v5 subprotocol's per-stream close
        # frames (channel 255) are left to the caller to ignore: the websocket
        # close frame above is what ends the stream.
        if len(data) > 1:
            yield data[0], data[1:]

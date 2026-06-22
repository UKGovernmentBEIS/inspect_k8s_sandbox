class LimitedBuffer:
    """
    A buffer with a limited capacity.

    Once the buffer is full, `truncated` is set and further appends are ignored.

    The buffer can be converted to a string (utf-8). Invalid utf-8 bytes are replaced
    with the unicode replacement character; an incomplete trailing character left by
    truncation is dropped.
    """

    def __init__(self, limit: int) -> None:
        self._buffer = bytearray()
        self._limit = limit
        self.truncated = False

    def append(self, data: bytes) -> None:
        if self.truncated:
            return
        remaining_space = self._limit - len(self._buffer)
        if len(data) > remaining_space:
            self.truncated = True
        self._buffer.extend(data[:remaining_space])

    def __str__(self) -> str:
        try:
            return self._buffer.decode("utf-8", errors="strict")
        except UnicodeDecodeError as e:
            # Drop an incomplete character left at the very end by truncation; replace
            # any other invalid bytes (e.g. binary subprocess output) rather than raise.
            if self.truncated and e.end == len(self._buffer):
                return self._buffer[: e.start].decode("utf-8", errors="strict")
            return self._buffer.decode("utf-8", errors="replace")

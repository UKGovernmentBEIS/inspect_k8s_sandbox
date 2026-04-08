class LimitedBuffer:
    """
    A buffer with a limited capacity.

    Once the buffer is full, `truncated` is set and further appends are ignored.

    The buffer can be converted to a string (utf-8). Will raise a UnicodeDecodeError if
    the buffer contains invalid utf-8 data (except it it was as a result of truncation).
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
        # If we're truncated the data, there may be an incomplete character right at the
        # end of the buffer.
        try:
            return self._buffer.decode("utf-8", errors="strict")
        except UnicodeDecodeError as e:
            if self.truncated and e.end == len(self._buffer):
                return self._buffer[: e.start].decode("utf-8", errors="strict")
            raise

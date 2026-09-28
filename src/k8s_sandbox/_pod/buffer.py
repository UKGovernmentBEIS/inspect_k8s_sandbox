class LimitedBuffer:
    """
    A buffer with a limited capacity.

    Once the buffer is full, `truncated` is set and further appends are ignored.

    The buffer can be converted to a string (utf-8). Invalid utf-8 bytes — including an
    incomplete trailing character left by truncation — are replaced with the unicode
    replacement character.
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
        return self._buffer.decode("utf-8", errors="replace")

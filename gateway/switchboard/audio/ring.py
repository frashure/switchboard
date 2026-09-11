class RingBuffer:
    """Accumulates PCM audio for the current utterance capture, capped at a
    max duration (design.md VAD default: max utterance ~15s) so a stuck
    CAPTURE can't grow unbounded."""

    def __init__(self, max_bytes: int):
        self._max_bytes = max_bytes
        self._buf = bytearray()

    def append(self, chunk: bytes) -> bool:
        """Returns False once max_bytes is reached -- caller should treat
        that as a forced end-of-speech."""
        self._buf.extend(chunk)
        return len(self._buf) < self._max_bytes

    def get_bytes(self) -> bytes:
        return bytes(self._buf)

    def clear(self) -> None:
        self._buf.clear()

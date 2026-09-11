"""Silero VAD wrapper for end-of-speech detection (design.md Section 4).

NOTE: unlike the OWUI/Wyoming clients, this has not yet been exercised
against real audio in this session -- the silero-vad package API (chunk
size, VADIterator constructor args) is implemented here from documented
usage patterns but should be sanity-checked with a real audio stream
before relying on it (M1/M3 exit criteria)."""

import numpy as np
from silero_vad import VADIterator, load_silero_vad

from switchboard.config import settings

_SAMPLE_RATE = 16000
_FRAME_SAMPLES = 512  # silero-vad's required chunk size at 16kHz
_FRAME_BYTES = _FRAME_SAMPLES * 2  # 16-bit samples


class EndOfSpeechDetector:
    """Feed 16-bit/16kHz/mono PCM chunks in via process(); returns True once
    silero-vad reports the end of a speech segment (per vad_pause_threshold_ms)."""

    def __init__(self):
        model = load_silero_vad()
        self._iterator = VADIterator(
            model,
            sampling_rate=_SAMPLE_RATE,
            min_silence_duration_ms=settings.vad_pause_threshold_ms,
        )
        self._pending = bytearray()

    def process(self, chunk: bytes) -> bool:
        self._pending.extend(chunk)
        end_of_speech = False
        while len(self._pending) >= _FRAME_BYTES:
            frame = bytes(self._pending[:_FRAME_BYTES])
            del self._pending[:_FRAME_BYTES]
            samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
            result = self._iterator(samples, return_seconds=True)
            if result and "end" in result:
                end_of_speech = True
        return end_of_speech

    def reset(self) -> None:
        self._iterator.reset_states()
        self._pending.clear()

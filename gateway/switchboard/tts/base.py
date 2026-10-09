from dataclasses import dataclass
from typing import Protocol

from switchboard.profiles import Profile


@dataclass
class SynthesizedAudio:
    audio: bytes
    rate: int
    width: int
    channels: int


class TtsBackend(Protocol):
    name: str

    async def synthesize(self, text: str, profile: Profile) -> SynthesizedAudio:
        """Speak `text` in `profile`'s voice for this backend."""

    async def is_ready(self) -> bool:
        """Cheap check that a synthesize() call is expected to succeed now."""

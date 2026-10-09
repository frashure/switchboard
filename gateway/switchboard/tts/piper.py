"""Wyoming v2 TTS client. Protocol confirmed working in scripts/wyoming_test.py."""

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize, SynthesizeVoice

from switchboard.config import settings
from switchboard.profiles import Profile
from switchboard.tts.base import SynthesizedAudio


async def synthesize(text: str, voice: str) -> SynthesizedAudio:
    audio = bytearray()
    rate = width = channels = None
    async with AsyncTcpClient.from_uri(settings.piper_uri) as client:
        await client.write_event(Synthesize(text=text, voice=SynthesizeVoice(name=voice)).event())
        while True:
            event = await client.read_event()
            if event is None:
                break
            if AudioStart.is_type(event.type):
                start = AudioStart.from_event(event)
                rate, width, channels = start.rate, start.width, start.channels
            elif AudioChunk.is_type(event.type):
                audio.extend(AudioChunk.from_event(event).audio)
            elif AudioStop.is_type(event.type):
                break

    if rate is None:
        raise RuntimeError(f"Piper never sent AudioStart for voice {voice!r}")
    return SynthesizedAudio(audio=bytes(audio), rate=rate, width=width, channels=channels)


class PiperBackend:
    name = "piper"

    async def synthesize(self, text: str, profile: Profile) -> SynthesizedAudio:
        return await synthesize(text, profile.voice)

    async def is_ready(self) -> bool:
        return True  # no health probe: failures surface (and are handled) when used

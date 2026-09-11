"""Wyoming v2 STT client. Protocol confirmed working in scripts/wyoming_test.py."""

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient

from switchboard.config import settings


async def transcribe(audio: bytes, rate: int = 16000, width: int = 2, channels: int = 1) -> str:
    async with AsyncTcpClient.from_uri(settings.whisper_uri) as client:
        await client.write_event(Transcribe().event())
        await client.write_event(AudioStart(rate=rate, width=width, channels=channels).event())

        chunk_size = rate * width * channels // 10  # ~100ms chunks
        for i in range(0, len(audio), chunk_size):
            piece = audio[i : i + chunk_size]
            await client.write_event(
                AudioChunk(audio=piece, rate=rate, width=width, channels=channels).event()
            )
        await client.write_event(AudioStop().event())

        while True:
            event = await client.read_event()
            if event is None:
                return ""
            if Transcript.is_type(event.type):
                return Transcript.from_event(event).text

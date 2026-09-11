"""
Wyoming v2 connectivity + round-trip spike: confirm the running Whisper and
Piper containers speak Wyoming and actually work, before building
gateway/switchboard/stt/whisper.py and tts/piper.py around them.

Two checks:
  1. Describe: connect to each service and print what it reports
     (confirms basic Wyoming framing works, and shows available
     models/voices).
  2. Round trip: synthesize a known phrase with Piper, then feed that same
     audio into Whisper and compare the transcript against the original --
     a self-contained functional test that needs no external test WAV.

This is throwaway exploration code, not part of the Gateway package.

Setup:
    pip install wyoming

Usage:
    export WHISPER_URI=tcp://localhost:10300   # adjust to your setup
    export PIPER_URI=tcp://localhost:10200     # adjust to your setup
    export PIPER_VOICE=en_US-lessac-medium     # optional; omit to use the server's default
    python scripts/wyoming_test.py
"""

import asyncio
import os
import wave
from pathlib import Path

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.info import Describe, Info
from wyoming.tts import Synthesize, SynthesizeVoice

WHISPER_URI = os.environ.get("WHISPER_URI", "tcp://localhost:10300")
PIPER_URI = os.environ.get("PIPER_URI", "tcp://localhost:10200")
PIPER_VOICE = os.environ.get("PIPER_VOICE")
PHRASE = "The quick brown fox jumps over the lazy dog"

OUT_WAV = Path("wyoming_test_output.wav")


async def describe(uri: str, label: str) -> None:
    print(f"\n--- Describe: {label} ({uri}) ---")
    async with AsyncTcpClient.from_uri(uri) as client:
        await client.write_event(Describe().event())
        event = await client.read_event()
        if event is None:
            print("  (no response -- connection closed)")
            return
        if Info.is_type(event.type):
            info = Info.from_event(event)
            for asr in info.asr:
                print(f"  asr program={asr.name!r} models={[m.name for m in asr.models]}")
            for tts in info.tts:
                print(f"  tts program={tts.name!r} voices={[v.name for v in tts.voices][:10]}")
        else:
            print(f"  unexpected event type: {event.type!r}")


async def synthesize(uri: str, text: str, voice: str | None) -> tuple[bytes, int, int, int]:
    """Returns (raw_pcm_bytes, rate, width, channels)."""
    audio = bytearray()
    rate = width = channels = None
    async with AsyncTcpClient.from_uri(uri) as client:
        synth_voice = SynthesizeVoice(name=voice) if voice else None
        await client.write_event(Synthesize(text=text, voice=synth_voice).event())
        while True:
            event = await client.read_event()
            if event is None:
                break
            if AudioStart.is_type(event.type):
                start = AudioStart.from_event(event)
                rate, width, channels = start.rate, start.width, start.channels
                print(f"  AudioStart: rate={rate} width={width} channels={channels}")
            elif AudioChunk.is_type(event.type):
                chunk = AudioChunk.from_event(event)
                audio.extend(chunk.audio)
            elif AudioStop.is_type(event.type):
                break
    if rate is None:
        raise RuntimeError("Piper never sent AudioStart -- check PIPER_URI/PIPER_VOICE")
    return bytes(audio), rate, width, channels


async def transcribe(uri: str, audio: bytes, rate: int, width: int, channels: int) -> str:
    async with AsyncTcpClient.from_uri(uri) as client:
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


async def main() -> None:
    await describe(WHISPER_URI, "Whisper (STT)")
    await describe(PIPER_URI, "Piper (TTS)")

    print(f"\n--- Round trip: synthesizing {PHRASE!r} via Piper ---")
    audio, rate, width, channels = await synthesize(PIPER_URI, PHRASE, PIPER_VOICE)
    print(f"  got {len(audio)} bytes of PCM audio")

    with wave.open(str(OUT_WAV), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(audio)
    print(f"  saved to {OUT_WAV} -- play it to sanity-check the voice sounds right")

    print(f"\n--- Feeding that audio into Whisper (rate={rate}, note: Whisper commonly expects 16kHz) ---")
    text = await transcribe(WHISPER_URI, audio, rate, width, channels)
    print(f"  transcript: {text!r}")

    print("\n=== Result ===")
    print(f"  original:   {PHRASE!r}")
    print(f"  transcript: {text!r}")
    if text.strip().lower().rstrip(".") == PHRASE.lower():
        print("  MATCH -- Wyoming v2 round trip confirmed working.")
    else:
        print(
            "  Not an exact match -- could be normal STT variance, or a sample-rate "
            f"mismatch (Piper gave {rate}Hz; if Whisper expects 16kHz specifically, "
            "try setting PIPER_VOICE to a 'low' quality voice, which is usually 16kHz)."
        )


if __name__ == "__main__":
    asyncio.run(main())

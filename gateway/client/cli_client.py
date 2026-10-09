"""CLI test client: WAV(s) in -> transcript + text + TTS out, through the
real Gateway /ws endpoint (design.md M1 exit criteria). Exercises the whole
pipeline with zero hardware. Supports multiple WAV files as sequential
turns in one session -- useful for testing multi-turn conversation
continuity (chat_id/parent_id threading), not just a single turn.

Setup:
    pip install websockets

Usage:
    python client/cli_client.py <persona_id> path/to/input.wav [more.wav ...]

With AUTH_MODE=owui on the Gateway, sign in by setting GATEWAY_EMAIL and
GATEWAY_PASSWORD (an Open WebUI account); nothing is stored.
"""

import asyncio
import json
import os
import sys
import wave

import numpy as np
import websockets

GATEWAY_WS = os.environ.get("GATEWAY_WS", "ws://localhost:8000/ws")


def login_cookie() -> dict:
    """Headers carrying the session cookie, if credentials were provided."""
    email, password = os.environ.get("GATEWAY_EMAIL"), os.environ.get("GATEWAY_PASSWORD")
    if not (email and password):
        return {}
    import json
    import urllib.request

    base = GATEWAY_WS.replace("wss://", "https://").replace("ws://", "http://").rsplit("/ws", 1)[0]
    request = urllib.request.Request(
        f"{base}/auth/login",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        cookie = response.headers["Set-Cookie"].split(";")[0]
    return {"Cookie": cookie}

# The Gateway's /ws protocol always expects 16-bit/16kHz/mono PCM (design.md
# Section 2/7) -- the real device resamples on-device before sending. This
# client must do the same rather than forwarding whatever rate the source
# WAV happens to be: an earlier version skipped this, silently feeding
# mismatched-rate audio into the pipeline (STT/VAD both assume 16kHz).
TARGET_RATE = 16000


def resample_to_16k(pcm: bytes, src_rate: int, width: int, channels: int) -> bytes:
    if src_rate == TARGET_RATE and width == 2 and channels == 1:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    duration = len(samples) / src_rate
    out_length = int(duration * TARGET_RATE)
    src_indices = np.linspace(0, len(samples) - 1, out_length)
    resampled = np.interp(src_indices, np.arange(len(samples)), samples).astype(np.int16)
    return resampled.tobytes()


def load_wav_as_16k_pcm(wav_path: str) -> bytes:
    with wave.open(wav_path, "rb") as wf:
        rate, width, channels = wf.getframerate(), wf.getsampwidth(), wf.getnchannels()
        pcm = wf.readframes(wf.getnframes())
    return resample_to_16k(pcm, rate, width, channels)


async def run_turn(ws, pcm: bytes, turn_num: int) -> None:
    await ws.send(json.dumps({"type": "talk_start"}))
    chunk_size = TARGET_RATE * 2 // 10  # ~100ms chunks of 16-bit mono
    for i in range(0, len(pcm), chunk_size):
        await ws.send(pcm[i : i + chunk_size])
    await ws.send(json.dumps({"type": "talk_end"}))

    out_audio = bytearray()
    out_rate = out_width = out_channels = None
    async for message in ws:
        if isinstance(message, bytes):
            out_audio.extend(message)
            continue
        event = json.loads(message)
        print(f"turn {turn_num} [{event['type']}] {event}")
        if event["type"] == "audio_header":
            out_rate, out_width, out_channels = (
                event["sample_rate"],
                event["bits"] // 8,
                event["channels"],
            )
        if event["type"] == "done":
            break

    if out_audio and out_rate:
        out_path = f"cli_client_output_{turn_num}.wav"
        with wave.open(out_path, "wb") as wf:
            wf.setnchannels(out_channels)
            wf.setsampwidth(out_width)
            wf.setframerate(out_rate)
            wf.writeframes(bytes(out_audio))
        print(f"turn {turn_num}: saved response audio to {out_path}")


async def main(persona_id: str, wav_paths: list[str]) -> None:
    async with websockets.connect(GATEWAY_WS, additional_headers=login_cookie()) as ws:
        await ws.send(json.dumps({"type": "select_persona", "id": persona_id}))
        for turn_num, wav_path in enumerate(wav_paths, start=1):
            pcm = load_wav_as_16k_pcm(wav_path)
            await run_turn(ws, pcm, turn_num)


if __name__ == "__main__":
    persona_id = sys.argv[1]
    wav_paths = sys.argv[2:]
    asyncio.run(main(persona_id, wav_paths))

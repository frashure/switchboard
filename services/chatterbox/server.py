"""Chatterbox TTS service for the Switchboard Gateway.

POST /synthesize {"text", "voice"} -> raw 16-bit mono PCM (rate in X-Sample-Rate)
GET  /health                       -> status, warm-up progress, available voices

Two things here exist because of measurements on a ROCm GPU
(see scripts/chatterbox_bench and docs/design.md):

* S3Gen's convolutions are tuned by MIOpen per input shape, and the shape
  follows the chunk's speech-token count, which varies continuously. A new
  length cost ~4s the first time, enough to stall streaming playback. So the
  speech tokens are padded with the silence token to a multiple of BUCKET,
  the padding's samples are trimmed off afterwards, and every bucket is
  pre-tuned in the background at startup. (Mel frames are a fixed multiple of
  tokens, so bounding token counts bounds every downstream shape.)
* The model keeps one active voice; prepared voice conditioning is cached per
  voice and swapped in under a lock, since generation isn't thread-safe.
"""

import asyncio
import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import torch
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

logger = logging.getLogger("chatterbox-service")
logging.basicConfig(level=logging.INFO)

MODEL = os.getenv("CHATTERBOX_MODEL", "turbo")  # "turbo" or "nano"
DEVICE = os.getenv("CHATTERBOX_DEVICE", "cuda")  # ROCm torch also calls it "cuda"
BUCKET = int(os.getenv("CHATTERBOX_BUCKET", "32"))  # 0 disables bucketing
WARM_MAX_TOKENS = int(os.getenv("CHATTERBOX_WARM_MAX_TOKENS", "640"))  # 25 tokens = 1s of speech
WARMUP = os.getenv("CHATTERBOX_WARMUP", "1") == "1"
VOICES_DIR = Path(os.getenv("CHATTERBOX_VOICES_DIR", "/voices"))
MAX_TEXT_CHARS = 1500
S3GEN_SIL = 4299  # Chatterbox's silence speech token (models/s3gen/const.py)
VOICE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
DEFAULT_VOICE = "default"


class Engine:
    def __init__(self) -> None:
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        self.model = ChatterboxTurboTTS.from_pretrained(device=DEVICE, nano=(MODEL == "nano"))
        self.sample_rate: int = self.model.sr
        self._lock = threading.Lock()
        self._voice_conds = {DEFAULT_VOICE: self.model.conds}  # built-in voice
        self.status = "loading"
        self.warmed = 0
        self.total_buckets = 0
        if BUCKET:
            self._install_bucketing()

    # ---- voices ----

    def voices(self) -> list[str]:
        clips = sorted(p.stem for p in VOICES_DIR.glob("*.wav")) if VOICES_DIR.is_dir() else []
        return [DEFAULT_VOICE, *[c for c in clips if c != DEFAULT_VOICE]]

    def _conds_for(self, voice: str):
        if voice in self._voice_conds:
            return self._voice_conds[voice]
        if not VOICE_NAME.match(voice):
            raise KeyError(voice)
        path = VOICES_DIR / f"{voice}.wav"
        if not path.is_file():
            raise KeyError(voice)
        self.model.prepare_conditionals(str(path))  # sets model.conds
        self._voice_conds[voice] = self.model.conds
        return self.model.conds

    # ---- synthesis ----

    def synthesize(self, text: str, voice: str) -> bytes:
        with self._lock:
            self.model.conds = self._conds_for(voice)
            wav = self.model.generate(text)
        samples = wav.detach().float().cpu().numpy().reshape(-1)
        return (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()

    # ---- shape bucketing ----

    def _install_bucketing(self) -> None:
        original = self.model.s3gen.inference

        def bucketed(speech_tokens, *args, **kwargs):
            n = speech_tokens.shape[-1]
            pad = (-n) % BUCKET
            if pad:
                filler = torch.full((pad,), S3GEN_SIL, dtype=speech_tokens.dtype, device=speech_tokens.device)
                speech_tokens = torch.cat([speech_tokens.reshape(-1), filler])
            wav, source = original(speech_tokens, *args, **kwargs)
            if pad:
                per_token = wav.shape[-1] / (n + pad)
                wav = wav[..., : wav.shape[-1] - int(round(pad * per_token))]
            return wav, source

        self.model.s3gen.inference = bucketed

    def warm_up(self) -> None:
        """Pre-tune every bucket shape. Small buckets first: most chunks (and
        always the first, latency-critical one) are short. The lock is taken
        per bucket, so a request arriving mid-warm-up waits at most one."""
        sizes = list(range(BUCKET, WARM_MAX_TOKENS + 1, BUCKET)) if BUCKET else []
        self.total_buckets = len(sizes)
        self.status = "warming"
        start = time.perf_counter()
        for size in sizes:
            tokens = torch.full((size,), S3GEN_SIL, dtype=torch.long, device=self.model.device)
            with self._lock:
                self.model.s3gen.inference(
                    speech_tokens=tokens, ref_dict=self._voice_conds[DEFAULT_VOICE].gen, n_cfm_timesteps=2
                )
            self.warmed += 1
            logger.info("warmed bucket %d/%d (%d tokens)", self.warmed, self.total_buckets, size)
        self.status = "ready"
        logger.info("ready (warm-up took %.0fs)", time.perf_counter() - start)


engine: Engine | None = None
startup_error: str | None = None


def _start() -> None:
    global engine, startup_error
    try:
        engine = Engine()
        if WARMUP:
            engine.warm_up()
        else:
            engine.status = "ready"
    except Exception as error:
        logger.exception("engine failed to start")
        startup_error = f"{type(error).__name__}: {error}"
        if engine is not None:
            engine.status = "failed"


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Load in a thread so /health answers ("loading") while weights download.
    threading.Thread(target=_start, daemon=True).start()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    if engine is None:
        if startup_error:
            return {"status": "failed", "model": MODEL, "error": startup_error}
        return {"status": "loading", "model": MODEL}
    return {
        "status": engine.status,
        "model": MODEL,
        "sample_rate": engine.sample_rate,
        "warmed_buckets": engine.warmed,
        "total_buckets": engine.total_buckets,
        "voices": engine.voices(),
    }


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    voice: str = DEFAULT_VOICE


@app.post("/synthesize")
async def synthesize(request: SynthesizeRequest) -> Response:
    if engine is None or engine.status in ("loading", "failed"):
        raise HTTPException(503, f"engine {engine.status if engine else ('failed' if startup_error else 'loading')}")
    try:
        pcm = await asyncio.to_thread(engine.synthesize, request.text, request.voice)
    except KeyError:
        raise HTTPException(404, f"unknown voice {request.voice!r}") from None
    return Response(
        content=pcm,
        media_type="application/octet-stream",
        headers={"X-Sample-Rate": str(engine.sample_rate), "X-Channels": "1", "X-Bits": "16"},
    )

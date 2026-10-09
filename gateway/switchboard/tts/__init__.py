"""Text-to-speech backends and the per-answer router.

Piper (Wyoming) is fast and always available; Chatterbox sounds much better
but runs as a separate GPU service that needs time to warm up. The router
picks Chatterbox when it is ready and otherwise Piper -- and does so once
per *answer*, because an answer's audio is announced with a single sample
rate (Piper 22.05 kHz, Chatterbox 24 kHz), so backends can't be mixed
mid-answer.
"""

import logging
import time

from switchboard.config import settings
from switchboard.profiles import Profile
from switchboard.tts.base import SynthesizedAudio, TtsBackend
from switchboard.tts.piper import PiperBackend

logger = logging.getLogger(__name__)

HEALTH_TTL_S = 5.0


class AnswerVoice:
    """Synthesizes one answer's chunks with a single backend. If the preferred
    backend fails on the *first* chunk (nothing has been sent yet), the whole
    answer falls back to Piper; a failure after that is a real error."""

    def __init__(self, profile: Profile, backend: TtsBackend, fallback: TtsBackend | None, on_failure=None):
        self._profile = profile
        self._backend = backend
        self._fallback = fallback
        self._on_failure = on_failure
        self._started = False

    async def synthesize(self, text: str) -> SynthesizedAudio:
        try:
            audio = await self._backend.synthesize(text, self._profile)
        except Exception:
            if self._started or self._fallback is None:
                raise
            logger.warning("%s failed on the first chunk; using %s for this answer",
                           self._backend.name, self._fallback.name, exc_info=True)
            if self._on_failure:
                self._on_failure()
            self._backend, self._fallback = self._fallback, None
            audio = await self._backend.synthesize(text, self._profile)
        self._started = True
        return audio


class TtsRouter:
    def __init__(self, preferred: TtsBackend, fallback: TtsBackend, clock=time.monotonic):
        self._preferred = preferred
        self._fallback = fallback
        self._clock = clock
        self._ready_until = 0.0  # preferred backend is trusted until this time
        self._unready_until = 0.0  # ...or distrusted until this time
        self._last_choice: str | None = None

    async def _preferred_ready(self) -> bool:
        now = self._clock()
        if now < self._ready_until:
            return True
        if now < self._unready_until:
            return False
        ready = await self._preferred.is_ready()
        if ready:
            self._ready_until = now + HEALTH_TTL_S
        else:
            self._unready_until = now + HEALTH_TTL_S
        return ready

    def _mark_failed(self) -> None:
        self._ready_until = 0.0
        self._unready_until = self._clock() + HEALTH_TTL_S

    async def for_answer(self, profile: Profile) -> AnswerVoice:
        if self._preferred is self._fallback or not await self._preferred_ready():
            choice, fallback = self._fallback, None
        else:
            choice, fallback = self._preferred, self._fallback
        if choice.name != self._last_choice:
            logger.info("TTS backend for answers: %s", choice.name)
            self._last_choice = choice.name
        return AnswerVoice(profile, choice, fallback, on_failure=self._mark_failed)


def build_router() -> TtsRouter:
    piper = PiperBackend()
    if settings.tts_backend == "chatterbox":
        from switchboard.tts.chatterbox import ChatterboxBackend

        return TtsRouter(ChatterboxBackend(), piper)
    return TtsRouter(piper, piper)


router = build_router()

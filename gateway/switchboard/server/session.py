"""Per-session state machine: IDLE / READY / CAPTURE / THINK / PLAY.
See docs/design.md Section 4 for the device-side counterpart."""

import asyncio
import logging
from enum import Enum, auto
from typing import Optional

from switchboard.audio.ring import RingBuffer
from switchboard.audio.vad import EndOfSpeechDetector
from switchboard.config import settings
from switchboard.llm.owui_client import OwuiClient
from switchboard.profiles import Profile, ProfileRegistry
from switchboard.stt.whisper import transcribe
from switchboard.text import strip_markdown
from switchboard.tts.piper import synthesize

logger = logging.getLogger(__name__)

RATE, WIDTH, CHANNELS = 16000, 2, 1


class SessionState(Enum):
    IDLE = auto()
    READY = auto()
    CAPTURE = auto()
    THINK = auto()
    PLAY = auto()


class Emitter:
    """Abstract sink for outgoing protocol messages -- implemented by
    server/ws.py wrapping the actual WebSocket."""

    async def status(self, state: str, detail: str | None = None) -> None:
        raise NotImplementedError

    async def transcript(self, text: str) -> None:
        raise NotImplementedError

    async def llm_text(self, text: str) -> None:
        raise NotImplementedError

    async def audio(self, pcm: bytes, rate: int, width: int, channels: int) -> None:
        raise NotImplementedError

    async def done(self) -> None:
        raise NotImplementedError


class Session:
    def __init__(self, profiles: ProfileRegistry, owui: OwuiClient, emitter: Emitter):
        self._profiles = profiles
        self._owui = owui
        self._emit = emitter

        self.state = SessionState.IDLE
        self._profile: Optional[Profile] = None
        self._chat_id: Optional[str] = None
        # Both must be threaded together on each send_turn call -- chat_id
        # alone isn't enough to continue the conversation thread, see
        # owui_client.py's send_turn docstring.
        self._parent_id: Optional[str] = None

        self._ring = RingBuffer(max_bytes=int(settings.vad_max_utterance_s * RATE * WIDTH * CHANNELS))
        self._vad = EndOfSpeechDetector()

    async def select_persona(self, persona_id: str) -> None:
        self._profile = self._profiles.get(persona_id)
        self._chat_id = None  # fresh conversation on persona switch
        self._parent_id = None
        self.state = SessionState.READY
        await self._emit.status("listening", detail=f"persona={persona_id}")

    async def talk_start(self) -> None:
        if self.state != SessionState.READY:
            return
        self.state = SessionState.CAPTURE
        self._ring.clear()
        self._vad.reset()

    async def audio_chunk(self, chunk: bytes) -> None:
        if self.state != SessionState.CAPTURE:
            return
        still_room = self._ring.append(chunk)
        # VAD inference is synchronous, CPU-bound torch work -- run it off
        # the event loop. Run per chunk on the main loop, it cumulatively
        # blocks the loop for the whole CAPTURE duration, which starves
        # background tasks like the OWUI socket.io client's keepalive
        # handling and was observed to cause the LLM step to hang afterward.
        end_of_speech = await asyncio.to_thread(self._vad.process, chunk)
        if end_of_speech:
            await self._finish_capture(reason="vad")
        elif not still_room:
            await self._finish_capture(reason="max_duration")

    async def talk_end(self) -> None:
        if self.state != SessionState.CAPTURE:
            return
        # talk_end is a hint/cancel-safety, not the sole end-of-turn signal
        # (docs/protocol.md) -- give the VAD a brief grace period to catch
        # the trailing pause on its own before forcing the cut.
        await asyncio.sleep(settings.vad_pause_threshold_ms / 1000)
        if self.state == SessionState.CAPTURE:
            await self._finish_capture(reason="talk_end_fallback")

    async def cancel(self) -> None:
        self._ring.clear()
        self.state = SessionState.READY if self._profile else SessionState.IDLE
        await self._emit.status("done")

    async def _set_chat_title(self, chat_id: str, title: str) -> None:
        try:
            await self._owui.set_chat_title(chat_id, title)
        except Exception:
            logger.exception("Failed to set chat title for chat_id=%s", chat_id)

    async def _thinking_heartbeat(self) -> None:
        """Keep re-emitting a status while waiting on the LLM turn, which can
        take up to ~2 minutes worst-case (owui_client.py REST fallback). A
        real turn silently exceeded 60s with zero traffic on the WS and the
        browser's connection was found disconnected by the time the answer
        was ready -- likely idle-connection handling on the browser/OS side."""
        try:
            while True:
                await asyncio.sleep(15)
                logger.info("heartbeat firing")
                await self._emit.status("thinking", detail="still_waiting_on_llm")
        except asyncio.CancelledError:
            pass

    async def _finish_capture(self, reason: str) -> None:
        self.state = SessionState.THINK
        audio = self._ring.get_bytes()
        self._ring.clear()
        logger.info("Capture finished, reason=%s, bytes=%d", reason, len(audio))
        await self._emit.status("thinking", detail=f"capture_end_reason={reason}")

        try:
            transcript = await asyncio.wait_for(
                transcribe(audio, RATE, WIDTH, CHANNELS), timeout=settings.stt_timeout
            )
        except Exception:
            logger.exception("STT failed")
            await self._emit.status("error", detail="stt_failed")
            self.state = SessionState.READY
            return
        await self._emit.transcript(transcript)

        is_new_conversation = self._chat_id is None
        heartbeat = asyncio.create_task(self._thinking_heartbeat())
        try:
            # send_turn manages its own internal timeout budget, including
            # a REST-based recovery path if the socket delivery is flaky --
            # see owui_client.py. That path can take up to ~120s worst case;
            # the heartbeat above keeps the WS connection alive during long
            # waits -- a real turn silently took over 60s once and the
            # browser's connection was found disconnected by the time the
            # answer was ready, even though generation succeeded server-side.
            final_text, chat_id, assistant_message_id = await self._owui.send_turn(
                self._profile, transcript, chat_id=self._chat_id, parent_id=self._parent_id
            )
            self._chat_id = chat_id
            self._parent_id = assistant_message_id
            if is_new_conversation:
                title = transcript.strip()[:50]
                asyncio.create_task(self._set_chat_title(chat_id, title))
        except Exception:
            logger.exception("LLM turn failed")
            await self._emit.status("error", detail="llm_failed")
            self.state = SessionState.READY
            return
        finally:
            heartbeat.cancel()
        await self._emit.llm_text(final_text)

        self.state = SessionState.PLAY
        await self._emit.status("speaking")
        try:
            # Strip markdown for speech only -- llm_text above still carries
            # the raw text in case a future display ever renders it.
            speech_text = strip_markdown(final_text)
            synthesized = await asyncio.wait_for(
                synthesize(speech_text, self._profile.voice), timeout=settings.tts_timeout
            )
            await self._emit.audio(
                synthesized.audio, synthesized.rate, synthesized.width, synthesized.channels
            )
        except Exception:
            logger.exception("TTS failed")
            await self._emit.status("error", detail="tts_failed")
            # llm_text already reached the screen -- degrade gracefully rather
            # than losing the turn entirely.

        self.state = SessionState.READY
        await self._emit.done()

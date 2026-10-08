"""WebSocket protocol handler (ESP32 <-> Gateway). See docs/design.md Section 7."""

import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from switchboard.llm.owui_client import OwuiClient
from switchboard.profiles import ProfileRegistry
from switchboard.server.session import Emitter, Session

logger = logging.getLogger(__name__)


class WebSocketEmitter(Emitter):
    """If the underlying connection has already closed (observed in
    practice: a long LLM wait outlasting the browser's WS connection),
    sends raise -- there's nothing meaningful to do at that point, so log
    and swallow rather than crashing the whole handler with an unhandled
    exception. Session's own state machine has already moved on by the
    time this would happen."""

    def __init__(self, ws: WebSocket):
        self._ws = ws

    async def _send_json(self, payload: dict) -> None:
        try:
            await self._ws.send_json(payload)
        except Exception:
            logger.warning("Failed to send %s -- connection likely already closed", payload["type"])

    async def status(self, state: str, detail: str | None = None) -> None:
        await self._send_json({"type": "status", "state": state, "detail": detail})

    async def transcript(self, text: str) -> None:
        await self._send_json({"type": "transcript", "text": text})

    async def llm_text(self, text: str) -> None:
        await self._send_json({"type": "llm_text", "text": text})

    # Confirmed the hard way: a long answer's synthesized audio can run to
    # several MB, and sending it as one WS frame exceeds common client-side
    # frame-size limits (e.g. the `websockets` library's default 1MB cap) --
    # closes the connection outright. Chunk it instead; a real ESP32 client
    # couldn't handle a multi-MB single frame either way.
    AUDIO_CHUNK_BYTES = 32 * 1024

    async def audio(self, pcm: bytes, rate: int, width: int, channels: int) -> None:
        # JSON header, followed by one or more binary frames, per
        # docs/design.md Section 7 -- the client accumulates frames until
        # `done` arrives rather than assuming exactly one frame.
        await self._send_json(
            {"type": "audio_header", "sample_rate": rate, "bits": width * 8, "channels": channels}
        )
        try:
            for i in range(0, len(pcm), self.AUDIO_CHUNK_BYTES):
                await self._ws.send_bytes(pcm[i : i + self.AUDIO_CHUNK_BYTES])
        except Exception:
            logger.warning("Failed to send audio bytes -- connection likely already closed")

    async def stop_audio(self) -> None:
        # Only meaningful if TTS audio was already sent before cancel()
        # caught up with it -- the server can't un-send bytes already on
        # the wire, so this just tells the client to stop playback.
        await self._send_json({"type": "stop_audio"})

    async def done(self) -> None:
        await self._send_json({"type": "done"})


async def handle_connection(ws: WebSocket, profiles: ProfileRegistry, owui: OwuiClient) -> None:
    await ws.accept()
    session = Session(profiles, owui, WebSocketEmitter(ws))
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("text") is not None:
                await _handle_control(session, json.loads(message["text"]))
            elif message.get("bytes") is not None:
                await session.audio_chunk(message["bytes"])
    except WebSocketDisconnect:
        pass


async def _handle_control(session: Session, control: dict) -> None:
    msg_type = control.get("type")
    if msg_type == "select_persona":
        await session.select_persona(control["id"])
    elif msg_type == "talk_start":
        await session.talk_start()
    elif msg_type == "talk_end":
        await session.talk_end()
    elif msg_type == "cancel":
        await session.cancel()

"""Client for the Chatterbox TTS service (services/chatterbox)."""

import httpx

from switchboard.config import settings
from switchboard.profiles import Profile
from switchboard.tts.base import SynthesizedAudio


class ChatterboxBackend:
    name = "chatterbox"

    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self._base_url = (base_url or settings.chatterbox_url).rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout or settings.chatterbox_timeout)

    async def synthesize(self, text: str, profile: Profile) -> SynthesizedAudio:
        response = await self._client.post(
            f"{self._base_url}/synthesize",
            json={"text": text, "voice": profile.chatterbox_voice},
        )
        response.raise_for_status()
        return SynthesizedAudio(
            audio=response.content,
            rate=int(response.headers["X-Sample-Rate"]),
            width=int(response.headers.get("X-Bits", "16")) // 8,
            channels=int(response.headers.get("X-Channels", "1")),
        )

    async def is_ready(self) -> bool:
        """True once the service has loaded the model *and* pre-tuned its
        shape buckets -- before that, a request can take seconds longer than
        a chunk takes to play, which would stall streaming."""
        try:
            response = await self._client.get(f"{self._base_url}/health", timeout=1.5)
            return response.status_code == 200 and response.json().get("status") == "ready"
        except (httpx.HTTPError, ValueError):
            return False

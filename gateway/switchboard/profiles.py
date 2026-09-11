"""Personas are discovered dynamically from OWUI's own model list at
runtime (see OwuiClient.list_models()) rather than hardcoded -- thin
client, minimal Gateway-side config. OWUI's `preset: true` models
(configured personas like "ophelia"/"phil", as opposed to raw base
models like "gemma4:31b-it-q4_K_M") already carry their own system
prompt and tool set server-side, so the Gateway no longer overrides
either -- only `model` (the OWUI model id) and `voice` (Piper voice,
which OWUI has no concept of) are Gateway-side config."""

from pathlib import Path

import yaml
from pydantic import BaseModel


class Profile(BaseModel):
    display_name: str
    model: str
    voice: str


class ProfileRegistry:
    def __init__(self, voice_config: dict):
        self._default_voice: str = voice_config["default_voice"]
        self._voice_overrides: dict[str, str] = voice_config.get("overrides", {})
        self._profiles: dict[str, Profile] = {}

    @classmethod
    def load_voice_config(cls, path: Path) -> dict:
        return yaml.safe_load(path.read_text())

    async def refresh(self, owui) -> None:
        """Rebuild the persona list from OWUI's current model list. Safe to
        call repeatedly (e.g. lazily on first use if this failed at
        startup, or periodically to pick up newly-added OWUI personas)."""
        models = await owui.list_models()
        self._profiles = {
            m["id"]: Profile(
                display_name=m.get("name", m["id"]),
                model=m["id"],
                voice=self._voice_overrides.get(m["id"], self._default_voice),
            )
            for m in models
            if m.get("preset")
        }

    def is_empty(self) -> bool:
        return not self._profiles

    def get(self, persona_id: str) -> Profile:
        try:
            return self._profiles[persona_id]
        except KeyError:
            raise ValueError(f"Unknown persona_id: {persona_id!r}") from None

    def list_ids(self) -> list[str]:
        return list(self._profiles.keys())

"""Personas are discovered dynamically from OWUI's own model list at
runtime (see OwuiClient.list_models()) rather than hardcoded -- thin
client, minimal Gateway-side config. OWUI's `preset: true` models
(configured personas like "ophelia"/"phil", as opposed to raw base
models like "gemma4:31b-it-q4_K_M") already carry their own system
prompt and tool set server-side, so the Gateway no longer overrides
either -- only `model` (the OWUI model id) and `voice` (Piper voice,
which OWUI has no concept of) are Gateway-side config."""

import asyncio
import logging
import time
from pathlib import Path

import yaml
from pydantic import BaseModel

# How often to re-check OWUI's model list once it's already been loaded
# successfully. Personas are expected to mature and change rarely once a
# workspace is set up, so this is a low-urgency safety net (picking up a
# newly-added/renamed persona within this window) rather than tight polling.
logger = logging.getLogger(__name__)

REFRESH_INTERVAL_S = 300


class Profile(BaseModel):
    display_name: str
    model: str
    voice: str  # Piper voice name
    chatterbox_voice: str = "default"  # reference-clip name in services/chatterbox/voices
    # data: URI (base64), or None if OWUI has no avatar configured for this
    # persona -- fetched per-persona since /api/models (list_models) doesn't
    # include it, only the per-model /api/v1/models/model endpoint does.
    avatar: str | None = None


class AvatarCache:
    """Avatars shared across users. Safe to share: a user's registry only
    ever asks for avatars of models Open WebUI listed for *that* user."""

    MISSING = object()

    def __init__(self, ttl_s: float = REFRESH_INTERVAL_S, clock=time.monotonic):
        self._ttl = ttl_s
        self._clock = clock
        self._items: dict[str, tuple[float, str | None]] = {}

    def get(self, model_id: str):
        item = self._items.get(model_id)
        if item is None or self._clock() - item[0] > self._ttl:
            return self.MISSING
        return item[1]

    def put(self, model_id: str, avatar: str | None) -> None:
        self._items[model_id] = (self._clock(), avatar)


class ProfileRegistry:
    def __init__(self, voice_config: dict, avatars: "AvatarCache | None" = None):
        self._avatars = avatars or AvatarCache()
        # A YAML key with nothing under it (e.g. `overrides:` with every entry
        # commented out) loads as None, not {} -- so every optional section
        # is normalised with `or`, never `.get(key, {})`.
        self._default_voice: str = voice_config["default_voice"]
        self._voice_overrides: dict[str, str] = voice_config.get("overrides") or {}
        chatterbox = voice_config.get("chatterbox") or {}
        self._chatterbox_default: str = chatterbox.get("default_voice") or "default"
        self._chatterbox_overrides: dict[str, str] = chatterbox.get("overrides") or {}
        self._profiles: dict[str, Profile] = {}
        self._last_refreshed: float = 0.0

    @classmethod
    def load_voice_config(cls, path: Path) -> dict:
        return yaml.safe_load(path.read_text())

    async def refresh(self, owui) -> None:
        """Rebuild the persona list from OWUI's current model list. Safe to
        call repeatedly (e.g. lazily on first use if this failed at
        startup, or periodically to pick up newly-added OWUI personas)."""
        models = [m for m in await owui.list_models() if m.get("preset")]

        async def fetch_avatar(model_id: str) -> str | None:
            cached = self._avatars.get(model_id)
            if cached is not AvatarCache.MISSING:
                return cached
            try:
                avatar = await owui.get_model_avatar(model_id)
            except Exception:
                return None  # not cached: try again next refresh
            self._avatars.put(model_id, avatar)
            return avatar

        avatars = await asyncio.gather(*(fetch_avatar(m["id"]) for m in models))

        self._profiles = {
            m["id"]: Profile(
                display_name=m.get("name", m["id"]),
                model=m["id"],
                voice=self._voice_overrides.get(m["id"], self._default_voice),
                chatterbox_voice=self._chatterbox_overrides.get(m["id"], self._chatterbox_default),
                avatar=avatar,
            )
            for m, avatar in zip(models, avatars)
        }
        self._last_refreshed = time.monotonic()

    def needs_refresh(self) -> bool:
        return self.is_empty() or (time.monotonic() - self._last_refreshed) > REFRESH_INTERVAL_S

    def is_empty(self) -> bool:
        return not self._profiles

    def get(self, persona_id: str) -> Profile:
        try:
            return self._profiles[persona_id]
        except KeyError:
            raise ValueError(f"Unknown persona_id: {persona_id!r}") from None

    def list_ids(self) -> list[str]:
        return list(self._profiles.keys())

    def list_summaries(self) -> list[dict]:
        """Richer shape for clients that render a persona picker (avatars,
        display names) rather than just needing the bare ids."""
        return [
            {"id": pid, "display_name": p.display_name, "avatar": p.avatar}
            for pid, p in self._profiles.items()
        ]


class PersonaDirectory:
    """One ProfileRegistry per user, since Open WebUI decides which models
    each user may see. Voices come from the same voices.yaml for everyone."""

    def __init__(self, voice_config: dict, avatars: AvatarCache | None = None):
        self._voice_config = voice_config
        self._avatars = avatars or AvatarCache()
        self._registries: dict[str, ProfileRegistry] = {}

    def registry_for(self, key: str) -> ProfileRegistry:
        if key not in self._registries:
            self._registries[key] = ProfileRegistry(self._voice_config, self._avatars)
        return self._registries[key]

    async def ensure(self, key: str, owui) -> ProfileRegistry:
        """The user's registry, (re)loaded from Open WebUI when empty or
        stale. Failures other than "token rejected" are logged and leave
        whatever was loaded before (the caller retries on its next request)."""
        from switchboard.llm.owui_client import OwuiUnauthorized

        registry = self.registry_for(key)
        if registry.needs_refresh():
            try:
                await registry.refresh(owui)
            except OwuiUnauthorized:
                raise
            except Exception:
                logger.exception("Failed to discover personas from Open WebUI")
        return registry

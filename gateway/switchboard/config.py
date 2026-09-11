from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SWITCHBOARD_", env_file=".env")

    # Personas are discovered dynamically from OWUI's own model list
    # (profiles.py) -- this only configures the one thing OWUI has no
    # concept of: which Piper voice each persona uses.
    voices_path: Path = Path(__file__).resolve().parent.parent / "config" / "voices.yaml"

    owui_base_url: str = "http://localhost:3000"
    owui_email: str = ""
    owui_password: str = ""

    whisper_uri: str = "tcp://localhost:10300"
    piper_uri: str = "tcp://localhost:10200"

    # Per-turn timeouts (seconds). Every stage of the turn flow (design.md
    # Section 4) must have a defined timeout and fail back to READY rather
    # than hang indefinitely.
    stt_timeout: float = 15.0
    # llm_timeout is the primary Socket.IO delivery wait before falling back
    # to REST polling (owui_client.py); real successful direct-delivery
    # turns have consistently finished in 15-35s, so 60s here was mostly
    # just delaying the fallback (and, once, outlasting the browser's WS
    # connection during a long silent wait -- see the heartbeat in
    # session.py). Lowered to trigger the fallback sooner on genuine misses.
    llm_timeout: float = 35.0
    tts_timeout: float = 30.0

    # VAD (design.md Section 4 "Proposed VAD defaults", flagged there as
    # rough numbers needing tuning). 600ms was confirmed too aggressive in
    # real use -- cut off a real question mid-sentence during a natural
    # pause ("What role did religion play in the divide between Russia
    # <cut>"). Bumped to 1200ms; still tunable, formal tuning is M3's job.
    vad_pre_roll_ms: int = 300
    vad_pause_threshold_ms: int = 1200
    vad_max_utterance_s: float = 15.0

    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()

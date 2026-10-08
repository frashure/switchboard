from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Absolute, not ".env": a relative path silently loads nothing when the
    # Gateway is started from any directory other than gateway/ (it then
    # falls back to localhost defaults and hangs retrying OWUI).
    model_config = SettingsConfigDict(
        env_prefix="SWITCHBOARD_",
        env_file=Path(__file__).resolve().parent.parent / ".env",
    )

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
    # Budget for the REST stable-length poll that recovers the final answer
    # (owui_client.py _fetch_final_text_via_rest) -- this is what actually
    # bounds total turn latency, since it runs regardless of whether the
    # socket delivery succeeded. Was 180s (90 * 2s), which turned out too
    # short for real agentic turns: a multi-tool-call turn (calendar search
    # + note lookup + knowledge grep + note edit) legitimately took longer
    # than that server-side, and the Gateway gave up and reported
    # "llm_failed" even though OWUI had produced a real, complete answer.
    # Raised well past worst-case observed tool-chain latency.
    llm_rest_poll_timeout: float = 480.0

    # VAD (design.md Section 4 "Proposed VAD defaults", flagged there as
    # rough numbers needing tuning). 600ms was confirmed too aggressive in
    # real use -- cut off a real question mid-sentence during a natural
    # pause ("What role did religion play in the divide between Russia
    # <cut>"). Bumped to 1200ms; still tunable, formal tuning is M3's job.
    vad_pre_roll_ms: int = 300
    vad_pause_threshold_ms: int = 1200
    vad_max_utterance_s: float = 15.0

    # Directory of built web client files (the LVGL simulator build plus
    # virtual_device/) to serve at "/" -- one origin for page, /profiles and
    # /ws, which is what makes a single HTTPS endpoint (tailscale serve)
    # enough for a tablet. Unset in dev, where client files are served by
    # a separate static server.
    static_dir: Path | None = None

    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()

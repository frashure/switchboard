import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from switchboard.config import settings
from switchboard.llm.owui_client import OwuiClient
from switchboard.profiles import ProfileRegistry
from switchboard.server.ws import handle_connection

logger = logging.getLogger(__name__)

voice_config = ProfileRegistry.load_voice_config(settings.voices_path)
profiles = ProfileRegistry(voice_config)
owui = OwuiClient()


async def ensure_profiles_loaded() -> None:
    """Personas are discovered from OWUI's model list -- lazy-refresh if
    empty (startup discovery failed, or this is literally the first use,
    mirroring OwuiClient's own lazy-connect pattern) or stale (periodic
    safety net so a newly-added OWUI persona shows up without a Gateway
    restart -- see ProfileRegistry.REFRESH_INTERVAL_S)."""
    if profiles.needs_refresh():
        try:
            await profiles.refresh(owui)
        except Exception:
            logger.exception("Failed to discover personas from OWUI")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Best-effort at startup -- OWUI being momentarily unreachable shouldn't
    # take down the whole Gateway (observed repeatedly in dev: the login
    # call fails specifically at process startup even though the endpoint is
    # reachable moments before/after). Both connect and persona discovery
    # retry lazily on first real use if this didn't succeed here.
    try:
        await owui.connect()
        await profiles.refresh(owui)
    except Exception:
        logger.exception("OWUI startup setup failed -- will retry lazily on first use")
    yield
    await owui.disconnect()


app = FastAPI(lifespan=lifespan)

# The virtual device (client/virtual_device/) is served from a separate
# static-file origin (e.g. :8001) and fetches /profiles from the Gateway
# (:8090 in dev) -- that's cross-origin, so it needs CORS. Wide open here
# since this only ever runs on a trusted LAN in dev (design.md v1 scope:
# no auth, trusted network) -- revisit if that scope ever changes.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/profiles")
async def list_profiles() -> dict:
    await ensure_profiles_loaded()
    return {"personas": profiles.list_summaries()}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await ensure_profiles_loaded()
    await handle_connection(websocket, profiles, owui)


# Registered last: a mount at "/" would otherwise shadow the routes above.
if settings.static_dir is not None and settings.static_dir.is_dir():
    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="client")

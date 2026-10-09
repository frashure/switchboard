import logging
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from switchboard.auth import COOKIE_NAME, AuthUser, LoginThrottle, SessionCodec
from switchboard.config import Settings, settings as default_settings
from switchboard.llm import owui_auth
from switchboard.llm.owui_client import OwuiClient, OwuiUnauthorized
from switchboard.profiles import AvatarCache, PersonaDirectory, ProfileRegistry
from switchboard.server.ws import handle_connection

logger = logging.getLogger(__name__)

NO_STORE = {"Cache-Control": "no-store"}
WS_UNAUTHORIZED = 4401
WS_FORBIDDEN = 4403
SERVICE_KEY = "service"


@dataclass
class Principal:
    """Who a request acts as: the Open WebUI account whose token is used."""

    key: str  # persona-registry key
    owui: OwuiClient
    user: AuthUser | None  # None in AUTH_MODE=none (the shared service account)


class LoginBody(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


def create_app(
    cfg: Settings = default_settings,
    *,
    owui_factory: Callable[[str], OwuiClient] = OwuiClient.for_token,
    signin_fn=owui_auth.signin,
    service_owui: OwuiClient | None = None,
) -> FastAPI:
    voice_config = ProfileRegistry.load_voice_config(cfg.voices_path)
    directory = PersonaDirectory(voice_config, AvatarCache())
    throttle = LoginThrottle()
    use_auth = cfg.auth_mode == "owui"

    codec: SessionCodec | None = None
    if use_auth:
        secret = cfg.session_secret
        if not secret:
            secret = secrets.token_urlsafe(32)
            logger.warning(
                "SWITCHBOARD_SESSION_SECRET is not set: using a random one, so everyone is signed out "
                "whenever the Gateway restarts. Set it (e.g. `openssl rand -hex 32`) to keep logins."
            )
        codec = SessionCodec(secret)
    else:
        service_owui = service_owui or OwuiClient()

    # ---- identity ----

    def principal_for(cookies: dict[str, str]) -> Principal | None:
        if not use_auth:
            return Principal(SERVICE_KEY, service_owui, None)
        user = codec.decode(cookies.get(COOKIE_NAME))
        return Principal(user.id, owui_factory(user.token), user) if user else None

    def session_cookie(response: Response, request: Request, user: AuthUser) -> None:
        response.set_cookie(
            COOKIE_NAME,
            codec.encode(user),
            max_age=max(60, int(user.expires_at - time.time())),
            httponly=True,
            secure=request.url.scheme == "https",  # behind Tailscale Serve this is https
            samesite="lax",
            path="/",
        )

    def clear_cookie(response: Response) -> None:
        response.delete_cookie(COOKIE_NAME, path="/")

    # ---- app ----

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not use_auth:
            # Best-effort warm-up of the shared account: Open WebUI being
            # momentarily unreachable shouldn't take the Gateway down (seen
            # repeatedly in dev); both steps retry lazily on first use.
            try:
                await service_owui.connect()
                await directory.ensure(SERVICE_KEY, service_owui)
            except Exception:
                logger.exception("OWUI startup setup failed -- will retry lazily on first use")
        yield

    app = FastAPI(lifespan=lifespan)

    # Same-origin deployments don't need CORS; this serves separate dev
    # origins for the unauthenticated clients. Credentials are *not* allowed,
    # so another site cannot use a logged-in user's cookie through it.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/auth/me")
    async def me(request: Request) -> JSONResponse:
        principal = principal_for(request.cookies)
        return JSONResponse(
            {
                "auth_mode": cfg.auth_mode,
                "authenticated": principal is not None,
                "user": principal.user.public() if principal and principal.user else None,
            },
            headers=NO_STORE,
        )

    @app.post("/auth/login")
    async def login(body: LoginBody, request: Request) -> JSONResponse:
        if not use_auth:
            raise HTTPException(400, "login is disabled (AUTH_MODE=none)")
        email = body.email.strip().lower()
        key = f"{request.client.host if request.client else '?'}|{email}"
        wait = throttle.retry_after(key)
        if wait > 0:
            return JSONResponse(
                {"detail": "Too many attempts. Try again shortly."},
                status_code=429,
                headers={**NO_STORE, "Retry-After": str(int(wait) + 1)},
            )
        try:
            result = await signin_fn(body.email.strip(), body.password)
        except owui_auth.InvalidCredentials:
            throttle.failure(key)
            raise HTTPException(401, "Incorrect email or password") from None
        except owui_auth.LoginRateLimited:
            return JSONResponse(
                {"detail": "Open WebUI is limiting sign-in attempts. Try again in a minute."},
                status_code=429,
                headers={**NO_STORE, "Retry-After": "60"},
            )
        except owui_auth.LoginUnavailable as error:
            logger.warning("Open WebUI sign-in unavailable: %s", error)
            raise HTTPException(502, "Can't reach Open WebUI right now") from None

        throttle.success(key)
        user = AuthUser(
            id=result.user_id,
            name=result.name,
            email=result.email,
            role=result.role,
            token=result.token,
            expires_at=min(result.expires_at, time.time() + cfg.session_max_age_days * 86400),
        )
        response = JSONResponse({"user": user.public()}, headers=NO_STORE)
        session_cookie(response, request, user)
        return response

    @app.post("/auth/logout")
    async def logout() -> Response:
        response = Response(status_code=204, headers=NO_STORE)
        clear_cookie(response)
        return response

    @app.get("/profiles")
    async def list_profiles(request: Request) -> JSONResponse:
        principal = principal_for(request.cookies)
        if principal is None:
            return JSONResponse({"detail": "Not signed in"}, status_code=401, headers=NO_STORE)
        try:
            registry = await directory.ensure(principal.key, principal.owui)
        except OwuiUnauthorized:
            response = JSONResponse({"detail": "Session expired"}, status_code=401, headers=NO_STORE)
            clear_cookie(response)
            return response
        return JSONResponse({"personas": registry.list_summaries()}, headers=NO_STORE)

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        async def reject(code: int) -> None:
            # Accept first so browsers see the close *code* (a refused
            # handshake just looks like a generic network failure).
            await websocket.accept()
            await websocket.close(code=code)

        if use_auth:
            # Cross-site WebSocket hijacking guard (SameSite=Lax already keeps
            # the cookie off cross-site handshakes; this is the second lock).
            origin = websocket.headers.get("origin")
            if origin and urlparse(origin).netloc != websocket.headers.get("host"):
                return await reject(WS_FORBIDDEN)

        principal = principal_for(websocket.cookies)
        if principal is None:
            return await reject(WS_UNAUTHORIZED)
        try:
            registry = await directory.ensure(principal.key, principal.owui)
        except OwuiUnauthorized:
            return await reject(WS_UNAUTHORIZED)
        await handle_connection(websocket, registry, principal.owui)

    # Registered last: a mount at "/" would otherwise shadow the routes above.
    if cfg.static_dir is not None and cfg.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=cfg.static_dir, html=True), name="client")

    return app


app = create_app()

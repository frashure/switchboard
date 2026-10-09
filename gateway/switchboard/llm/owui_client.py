"""
Option C -- OWUI WebSocket chat-session client.

Implements the protocol confirmed in scripts/m0_owui_spike.py and recorded
in docs/protocol.md: connect Socket.IO, emit user-join, POST
/api/chat/completions with session_id=<sid> and a correctly-linked
user/assistant message-tree pair.

The socket is only used as a rough "has generation started" gate -- its own
terminal chat:completion/done:true events turned out not to reliably mark
true completion (multiple fire per turn; taking the first one's embedded
text silently truncated real answers). The actual final text always comes
from OWUI's own persisted chat record via REST polling
(_fetch_final_text_via_rest), which has been correct and complete every
time it's been checked against.

Each send_turn() opens its own fresh Socket.IO connection rather than
reusing one long-lived connection for the app's lifetime. This was found
the hard way: a shared connection reused across many sequential turns
intermittently dropped terminal events on later turns (reproduced several
times against a real OWUI instance), while repeated fresh-connection runs
(5/5, 16-33s each) were reliable every time. Only the login token is
cached at app lifetime -- that's a stateless REST call, not implicated.
Per-turn connect overhead is negligible next to STT/LLM/TTS latency.
"""

import asyncio
import time
import uuid

import httpx
import socketio

from switchboard.config import settings
from switchboard.profiles import Profile


# Minimum gap between login attempt bursts after a failure -- see connect().
LOGIN_COOLDOWN_S = 20


class OwuiUnauthorized(Exception):
    """Open WebUI rejected the token (expired, revoked, or never valid)."""


class OwuiClient:
    """Talks to Open WebUI as one account.

    Two modes: the *service* client logs in with the credentials in settings
    (single-account deployments, AUTH_MODE=none); a *user-bound* client
    (`for_token`) is handed a user's own token and never logs in itself --
    so chats, memory, tools and model access are that user's, not a shared
    account's."""

    def __init__(self, token: str | None = None):
        self._token: str | None = token
        self._bound = token is not None
        self._last_login_failure: float = float("-inf")
        self._last_login_error: str = ""

    @classmethod
    def for_token(cls, token: str) -> "OwuiClient":
        return cls(token=token)

    def _check(self, response: httpx.Response) -> None:
        """raise_for_status, but 401 becomes OwuiUnauthorized -- and a service
        client forgets its token so the next call logs in again."""
        if response.status_code == 401:
            if not self._bound:
                self._token = None
            raise OwuiUnauthorized()
        response.raise_for_status()

    async def connect(self) -> None:
        if self._bound:
            return  # a user's token is all there is; re-login is the user's to do
        # Observed repeatedly in this dev environment: the login call fails
        # with a transient connection error specifically during process
        # startup (uvicorn's ASGI lifespan), even though the identical
        # request as a standalone script succeeds immediately, and even
        # though the endpoint is reachable via curl/plain httpx moments
        # before and after. Looks like environment-specific network setup
        # settling right at process start, not an application bug -- retry
        # generously rather than failing app startup outright.
        # Cooldown after a failed login: with no token, every incoming
        # request calls connect() again, so an unreachable/rejecting OWUI
        # would otherwise be hammered once per request -- which also trips
        # OWUI's own sign-in rate limit (429) and keeps it tripped.
        since_failure = time.monotonic() - self._last_login_failure
        if since_failure < LOGIN_COOLDOWN_S:
            raise RuntimeError(
                f"OWUI login failed {since_failure:.0f}s ago; not retrying for "
                f"{LOGIN_COOLDOWN_S - since_failure:.0f}s more ({self._last_login_error})"
            )

        last_error = None
        attempts = 10
        for attempt in range(attempts):
            try:
                r = await asyncio.to_thread(
                    httpx.post,
                    f"{settings.owui_base_url}/api/v1/auths/signin",
                    json={"email": settings.owui_email, "password": settings.owui_password},
                    timeout=10,
                )
                r.raise_for_status()
                self._token = r.json()["token"]
                return
            except httpx.HTTPStatusError as e:
                # 4xx (bad credentials, rate-limited) won't fix itself within
                # seconds -- retrying only makes a 429 worse. 5xx is transient.
                last_error = e
                if e.response.status_code < 500:
                    break
                await asyncio.sleep(3)
            except httpx.HTTPError as e:
                last_error = e
                await asyncio.sleep(3)
        self._last_login_failure = time.monotonic()
        self._last_login_error = str(last_error)
        raise RuntimeError(f"Could not log in to OWUI: {last_error}")

    async def list_models(self) -> list[dict]:
        """GET /api/models -- confirmed: returns {"data": [...]}, each model
        with a `preset` field distinguishing configured personas
        (preset: true, e.g. "ophelia", "phil") from raw base models
        (preset: null/false, e.g. "gemma4:31b-it-q4_K_M")."""
        if self._token is None:
            await self.connect()
        headers = {"Authorization": f"Bearer {self._token}"}

        def _get() -> dict:
            r = httpx.get(f"{settings.owui_base_url}/api/models", headers=headers, timeout=10)
            self._check(r)
            return r.json()

        data = await asyncio.to_thread(_get)
        return data.get("data", [])

    async def get_model_avatar(self, model_id: str) -> str | None:
        """GET /api/v1/models/model?id=<id> -- confirmed: this per-model
        endpoint (unlike the bulk /api/models used by list_models) includes
        meta.profile_image_url, a data: URI (base64) of whatever avatar was
        set for the persona in OWUI's admin UI. Returns None if the model
        has no avatar configured or the field is missing."""
        if self._token is None:
            await self.connect()
        headers = {"Authorization": f"Bearer {self._token}"}

        def _get() -> dict:
            r = httpx.get(
                f"{settings.owui_base_url}/api/v1/models/model",
                params={"id": model_id},
                headers=headers,
                timeout=10,
            )
            self._check(r)
            return r.json()

        data = await asyncio.to_thread(_get)
        return data.get("meta", {}).get("profile_image_url") or None

    async def delete_chat(self, chat_id: str) -> None:
        """DELETE /api/v1/chats/{chat_id} -- REST convention inferred from
        the confirmed GET /api/v1/chats/{chat_id} endpoint; not yet
        independently verified against the live instance."""
        headers = {"Authorization": f"Bearer {self._token}"}
        r = await asyncio.to_thread(
            httpx.delete,
            f"{settings.owui_base_url}/api/v1/chats/{chat_id}",
            headers=headers,
            timeout=10,
        )
        self._check(r)

    async def set_chat_title(self, chat_id: str, title: str) -> None:
        """POST /api/v1/chats/{chat_id} with the full chat object (only
        `title` changed) -- confirmed working against the live instance.
        OWUI's own auto-titling (the `chat:title` socket event seen in real
        browser captures) never fired for our programmatically-created
        chats, for reasons not fully tracked down -- setting it ourselves
        is simpler than chasing that further for what's a cosmetic issue."""
        headers = {"Authorization": f"Bearer {self._token}"}

        def _get() -> dict:
            r = httpx.get(
                f"{settings.owui_base_url}/api/v1/chats/{chat_id}", headers=headers, timeout=10
            )
            self._check(r)
            return r.json()

        def _post(chat_obj: dict) -> None:
            r = httpx.post(
                f"{settings.owui_base_url}/api/v1/chats/{chat_id}",
                headers=headers,
                json={"chat": chat_obj},
                timeout=10,
            )
            self._check(r)

        data = await asyncio.to_thread(_get)
        chat_obj = data["chat"]
        chat_obj["title"] = title
        await asyncio.to_thread(_post, chat_obj)

    async def disconnect(self) -> None:
        pass  # nothing persistent to close -- see module docstring

    async def send_turn(
        self,
        profile: Profile,
        text: str,
        chat_id: str | None = None,
        parent_id: str | None = None,
    ) -> tuple[str, str, str]:
        """Run one turn. Returns (final_text, chat_id, assistant_message_id).
        The caller should pass chat_id AND assistant_message_id (as the next
        parent_id) back in to continue the same conversation thread -- omit
        both to start a fresh chat. Passing chat_id without the matching
        parent_id makes OWUI treat the new message as a sibling edit/branch
        of the first message rather than a continuation (confirmed the hard
        way: multi-turn testing showed every turn as "N/N" branches in the
        OWUI UI instead of a growing thread)."""
        if self._token is None:
            await self.connect()
        recent_events: list[dict] = []
        pending: dict[str, asyncio.Queue] = {}

        async def on_events(payload: dict) -> None:
            recent_events.append(payload)
            evt_chat_id = payload.get("chat_id")
            queue = pending.get(evt_chat_id)
            if queue is None:
                return
            data = payload.get("data", {})
            if data.get("type") == "chat:completion" and data.get("data", {}).get("done"):
                await queue.put(data["data"])

        sio = socketio.AsyncClient(logger=False, engineio_logger=False)
        sio.on("events", on_events)
        await sio.connect(
            settings.owui_base_url,
            socketio_path="/ws/socket.io",
            auth={"token": self._token},
            transports=["websocket"],
        )
        await sio.emit("user-join", {"auth": {"token": self._token}})

        try:
            user_message_id = str(uuid.uuid4())
            assistant_message_id = str(uuid.uuid4())
            payload = {
                "stream": True,
                "model": profile.model,
                # tool_ids/system prompt deliberately omitted -- the OWUI
                # model itself (a "preset") already has its own configured
                # tool set and system prompt server-side; overriding them
                # here would just duplicate config that can drift out of
                # sync with what's actually set up in OWUI.
                "session_id": sio.sid,
                "id": assistant_message_id,
                "message_ids": [
                    {"model_id": profile.model, "message_id": assistant_message_id, "modelIdx": 0}
                ],
                "parent_id": parent_id,
                "user_message": {
                    "id": user_message_id,
                    "parentId": parent_id,
                    "childrenIds": [assistant_message_id],
                    "role": "user",
                    "content": text,
                    "timestamp": int(time.time()),
                    "models": [profile.model],
                },
                "background_tasks": {"follow_up_generation": False},
            }
            if chat_id:
                payload["chat_id"] = chat_id

            headers = {"Authorization": f"Bearer {self._token}"}

            def _post() -> dict:
                r = httpx.post(
                    f"{settings.owui_base_url}/api/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=30,
                )
                self._check(r)
                return r.json()

            ack = await asyncio.to_thread(_post)
            turn_chat_id = ack["chat_id"]

            queue: asyncio.Queue = asyncio.Queue()
            pending[turn_chat_id] = queue

            # The socket's own "done" events turned out not to reliably mark
            # true completion (confirmed: multiple chat:completion/done:true
            # events fire per turn, and the first one's embedded text is
            # sometimes still a partial/interim answer -- taking it directly
            # silently truncated real responses). So the socket is now only
            # used as a rough "has anything happened yet" gate; the actual
            # answer always comes from OWUI's own persisted chat record via
            # REST (_fetch_final_text_via_rest), which has been reliable
            # every time it's been checked against, in full.
            already_seen = any(b.get("chat_id") == turn_chat_id for b in recent_events)
            if not already_seen:
                try:
                    await asyncio.wait_for(queue.get(), timeout=settings.llm_timeout)
                except asyncio.TimeoutError:
                    pass  # proceed to REST regardless -- see comment above
        finally:
            await sio.disconnect()

        final_text = await self._fetch_final_text_via_rest(turn_chat_id)
        return final_text, turn_chat_id, assistant_message_id

    async def _fetch_final_text_via_rest(self, chat_id: str) -> str:
        """Authoritative source for the final answer: OWUI's own persisted
        chat record, via GET /api/v1/chats/{chat_id}. Polls until the
        assistant message's text length is stable across two consecutive
        checks, rather than returning on the first non-empty snapshot --
        a still-generating answer keeps growing, and returning too early
        reproduces the same truncation the socket path had."""
        headers = {"Authorization": f"Bearer {self._token}"}

        def _get() -> dict:
            r = httpx.get(
                f"{settings.owui_base_url}/api/v1/chats/{chat_id}", headers=headers, timeout=10
            )
            self._check(r)
            return r.json()

        previous_length = -1
        stable_count = 0
        poll_interval_s = 2
        # Long, elaborate answers (essay-style, several thousand characters)
        # have been observed still actively growing past 60s of generation
        # -- confirmed via REST, not stalled, just genuinely slow for long
        # responses. Multi-tool-call turns (each tool round-trip re-invokes
        # the model) have been observed taking even longer. Budget is
        # configurable via settings.llm_rest_poll_timeout.
        max_polls = int(settings.llm_rest_poll_timeout / poll_interval_s)
        for _ in range(max_polls):
            data = await asyncio.to_thread(_get)
            history = data.get("chat", {}).get("history", {})
            current_id = history.get("currentId")
            message = history.get("messages", {}).get(current_id, {})
            content = message.get("content", "") if message.get("role") == "assistant" else ""
            if content and len(content) == previous_length:
                stable_count += 1
                if stable_count >= 2:
                    return content
            else:
                stable_count = 0
            previous_length = len(content)
            await asyncio.sleep(poll_interval_s)

        raise RuntimeError(f"Could not recover a stable final text for chat_id={chat_id} via REST polling")

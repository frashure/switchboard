import asyncio
import time
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from switchboard.auth import COOKIE_NAME, AuthUser, LoginThrottle, SessionCodec
from switchboard.config import Settings
from switchboard.llm import owui_auth
from switchboard.llm.owui_auth import InvalidCredentials, LoginRateLimited, LoginUnavailable, OwuiLogin
from switchboard.llm.owui_client import OwuiClient, OwuiUnauthorized
from switchboard.main import create_app

VOICES = Path(__file__).resolve().parent.parent / "config" / "voices.yaml"
SECRET = "test-secret"

# Two users, each allowed a different set of models.
USERS = {
    ("alice@example.com", "alice-pw"): ("alice", "tok-alice"),
    ("bob@example.com", "bob-pw"): ("bob", "tok-bob"),
}
MODELS = {
    "tok-alice": [("phil", "Phil"), ("libby", "Libby")],
    "tok-bob": [("ophelia", "Ophelia")],
}


class FakeOwui:
    def __init__(self, token):
        self.token = token

    async def list_models(self):
        if self.token not in MODELS:
            raise OwuiUnauthorized()
        return [{"id": i, "name": n, "preset": True} for i, n in MODELS[self.token]]

    async def get_model_avatar(self, model_id):
        return f"data:image/png;base64,{model_id}"

    async def connect(self):
        pass


async def fake_signin(email, password):
    found = USERS.get((email, password))
    if not found:
        raise InvalidCredentials()
    name, token = found
    return OwuiLogin(token=token, expires_at=time.time() + 86400, user_id=name, name=name.title(),
                     email=email, role="user")


def make_app(**overrides):
    cfg = Settings(auth_mode="owui", session_secret=SECRET, voices_path=VOICES, static_dir=None, **overrides)
    return create_app(cfg, owui_factory=FakeOwui, signin_fn=fake_signin)


@pytest.fixture(autouse=True)
def no_real_vad(monkeypatch):
    class DummyVad:
        def reset(self): ...
        def process(self, chunk): return False

    monkeypatch.setattr("switchboard.server.session.EndOfSpeechDetector", DummyVad)


def login(client, email="alice@example.com", password="alice-pw"):
    return client.post("/auth/login", json={"email": email, "password": password})


def persona_ids(client):
    return sorted(p["id"] for p in client.get("/profiles").json()["personas"])


# ---- sessions ----

def ws_close_code(client, **kwargs):
    """Open /ws, send a message, and return the code it is closed with.
    Sending first means a server that wrongly *accepts* answers instead of
    leaving the test blocked on a close that never comes."""
    with client.websocket_connect("/ws", **kwargs) as ws:
        ws.send_json({"type": "select_persona", "id": "phil"})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    return closed.value.code


def test_unauthenticated_requests_are_rejected():
    with TestClient(make_app()) as client:
        assert client.get("/profiles").status_code == 401
        me = client.get("/auth/me").json()
        assert me == {"auth_mode": "owui", "authenticated": False, "user": None}
        assert ws_close_code(client) == 4401


def test_login_sets_an_httponly_encrypted_cookie_and_hides_the_token():
    with TestClient(make_app()) as client:
        response = login(client)
        assert response.status_code == 200
        assert response.json()["user"]["email"] == "alice@example.com"
        assert "tok-alice" not in response.text  # the Open WebUI token never reaches the browser as JSON
        cookie = response.headers["set-cookie"]
        assert cookie.startswith(f"{COOKIE_NAME}=")
        assert "httponly" in cookie.lower() and "samesite=lax" in cookie.lower()
        assert "tok-alice" not in cookie  # encrypted, not just encoded
        assert client.get("/auth/me").json()["user"]["name"] == "Alice"


def test_each_user_only_sees_their_own_models():
    app = make_app()
    with TestClient(app) as alice, TestClient(app) as bob:
        login(alice)
        login(bob, "bob@example.com", "bob-pw")
        assert persona_ids(alice) == ["libby", "phil"]
        assert persona_ids(bob) == ["ophelia"]


def test_websocket_enforces_the_same_isolation():
    app = make_app()
    with TestClient(app) as alice:
        login(alice)
        with alice.websocket_connect("/ws") as ws:
            ws.send_json({"type": "select_persona", "id": "phil"})
            assert ws.receive_json()["state"] == "listening"
            ws.send_json({"type": "select_persona", "id": "ophelia"})  # Bob's model
            reply = ws.receive_json()
            assert (reply["state"], reply["detail"]) == ("error", "unknown_persona")


def test_logout_ends_the_session():
    with TestClient(make_app()) as client:
        login(client)
        assert client.post("/auth/logout").status_code == 204
        assert client.get("/profiles").status_code == 401


def test_tampered_foreign_and_expired_cookies_are_rejected():
    app = make_app()
    good = SessionCodec(SECRET).encode(AuthUser("alice", "Alice", "a@x", "user", "tok-alice", time.time() + 3600))
    expired = SessionCodec(SECRET).encode(AuthUser("alice", "Alice", "a@x", "user", "tok-alice", time.time() - 5))
    foreign = SessionCodec("other-secret").encode(AuthUser("alice", "Alice", "a@x", "user", "tok-alice", time.time() + 3600))

    def status(value):
        with TestClient(app) as client:
            client.cookies.set(COOKIE_NAME, value)
            return client.get("/profiles").status_code

    assert status(good) == 200
    assert status(good[:-4] + "AAAA") == 401  # tampered
    assert status(foreign) == 401  # signed with a different secret (e.g. rotated)
    assert status(expired) == 401
    assert status("not-a-cookie") == 401


def test_revoked_token_returns_401_and_clears_the_cookie():
    app = make_app()
    with TestClient(app) as client:
        login(client)
        MODELS.pop("tok-alice")  # Open WebUI now rejects this token
        try:
            response = client.get("/profiles")
        finally:
            MODELS["tok-alice"] = [("phil", "Phil"), ("libby", "Libby")]
        assert response.status_code == 401
        assert COOKIE_NAME in response.headers["set-cookie"] and "max-age=0" in response.headers["set-cookie"].lower()


def test_websocket_from_another_origin_is_refused():
    with TestClient(make_app()) as client:
        login(client)
        assert ws_close_code(client, headers={"origin": "https://evil.example"}) == 4403


# ---- login failures ----

def test_wrong_password_is_401_and_repeated_failures_are_throttled():
    with TestClient(make_app()) as client:
        for _ in range(5):
            assert login(client, password="wrong").status_code == 401
        blocked = login(client)  # even the right password waits out the throttle
        assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0


def test_open_webui_rate_limit_and_outage_are_reported_distinctly():
    async def limited(email, password): raise LoginRateLimited()
    async def down(email, password): raise LoginUnavailable("ConnectError")

    cfg = Settings(auth_mode="owui", session_secret=SECRET, voices_path=VOICES, static_dir=None)
    with TestClient(create_app(cfg, owui_factory=FakeOwui, signin_fn=limited)) as client:
        assert login(client).status_code == 429
    with TestClient(create_app(cfg, owui_factory=FakeOwui, signin_fn=down)) as client:
        assert login(client).status_code == 502


def test_session_lifetime_is_capped():
    app = make_app(session_max_age_days=1)

    async def long_lived(email, password):
        result = await fake_signin(email, password)
        return OwuiLogin(**{**result.__dict__, "expires_at": time.time() + 365 * 86400})

    cfg = Settings(auth_mode="owui", session_secret=SECRET, voices_path=VOICES, static_dir=None, session_max_age_days=1)
    with TestClient(create_app(cfg, owui_factory=FakeOwui, signin_fn=long_lived)) as client:
        header = login(client).headers["set-cookie"].lower()
        max_age = int(header.split("max-age=")[1].split(";")[0])
        assert max_age <= 86400


# ---- the no-login mode keeps working ----

def test_auth_mode_none_uses_the_service_account_without_login():
    cfg = Settings(auth_mode="none", voices_path=VOICES, static_dir=None)
    with TestClient(create_app(cfg, service_owui=FakeOwui("tok-bob"))) as client:
        assert client.get("/auth/me").json()["authenticated"] is True
        assert persona_ids(client) == ["ophelia"]
        assert login(client).status_code == 400


# ---- throttle, codec, and the Open WebUI sign-in parser ----

def test_throttle_window_slides():
    now = [0.0]
    throttle = LoginThrottle(max_failures=2, window_s=100, clock=lambda: now[0])
    throttle.failure("k"); throttle.failure("k")
    assert throttle.retry_after("k") == 100
    now[0] = 60
    assert throttle.retry_after("k") == 40
    now[0] = 101
    assert throttle.retry_after("k") == 0
    throttle.failure("k"); throttle.failure("k"); throttle.success("k")
    assert throttle.retry_after("k") == 0


def _mock_signin(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(owui_auth.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_signin_parses_the_response(monkeypatch):
    _mock_signin(monkeypatch, lambda r: httpx.Response(200, json={
        "token": "a.b.c", "expires_at": 1_900_000_000, "id": "u1", "name": "Alice", "email": "a@x", "role": "admin"}))
    result = asyncio.run(owui_auth.signin("a@x", "pw", base_url="http://owui"))
    assert (result.user_id, result.name, result.role, result.expires_at) == ("u1", "Alice", "admin", 1_900_000_000.0)


@pytest.mark.parametrize("status, error", [
    (400, InvalidCredentials), (401, InvalidCredentials), (429, LoginRateLimited), (500, LoginUnavailable),
])
def test_signin_maps_failures(monkeypatch, status, error):
    _mock_signin(monkeypatch, lambda r: httpx.Response(status, json={"detail": "x"}))
    with pytest.raises(error):
        asyncio.run(owui_auth.signin("a@x", "pw", base_url="http://owui"))


def test_signin_network_failure_does_not_leak_the_password(monkeypatch):
    def boom(request): raise httpx.ConnectError("refused: secret-password-here")
    _mock_signin(monkeypatch, boom)
    with pytest.raises(LoginUnavailable) as caught:
        asyncio.run(owui_auth.signin("a@x", "secret-password-here", base_url="http://owui"))
    assert "secret-password-here" not in str(caught.value)


# ---- the OWUI client's two modes ----

def test_user_bound_client_keeps_its_token_on_401_but_service_client_forgets_it():
    unauthorized = httpx.Response(401)
    bound = OwuiClient.for_token("user-token")
    with pytest.raises(OwuiUnauthorized):
        bound._check(unauthorized)
    assert bound._token == "user-token"

    service = OwuiClient()
    service._token = "service-token"
    with pytest.raises(OwuiUnauthorized):
        service._check(unauthorized)
    assert service._token is None  # will log in again on next use


def test_a_token_expiring_mid_turn_asks_the_client_to_sign_in_again(monkeypatch):
    from switchboard.profiles import ProfileRegistry
    from switchboard.server.session import Emitter, Session, SessionState

    class Recorder(Emitter):
        def __init__(self): self.statuses = []
        async def status(self, state, detail=None): self.statuses.append((state, detail))
        async def transcript(self, text): ...

    class ExpiredOwui(FakeOwui):
        async def send_turn(self, *a, **k): raise OwuiUnauthorized()

    async def fake_transcribe(*a, **k): return "hello"
    monkeypatch.setattr("switchboard.server.session.transcribe", fake_transcribe)

    async def go():
        owui = ExpiredOwui("tok-alice")
        registry = ProfileRegistry({"default_voice": "x"})
        await registry.refresh(owui)
        emitter = Recorder()
        session = Session(registry, owui, emitter)
        await session.select_persona("phil")
        await session._finish_capture("vad")
        return emitter.statuses, session.state

    statuses, state = asyncio.run(go())
    assert ("error", "session_expired") in statuses
    assert state == SessionState.READY

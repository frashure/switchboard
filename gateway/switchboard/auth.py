"""Login sessions for AUTH_MODE=owui.

A session is an encrypted, signed cookie that carries the user's Open WebUI
token -- no server-side session table. Two reasons: the Gateway can restart
(deploys) without logging everyone out, which matters for a tablet that is
only signed in about once a month; and there is nothing to expire or leak
server-side. The tradeoff is that sign-out clears the cookie but cannot
revoke the underlying Open WebUI token before it expires on its own.
"""

import base64
import hashlib
import json
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass

from cryptography.fernet import Fernet, InvalidToken

COOKIE_NAME = "sb_session"


@dataclass(frozen=True)
class AuthUser:
    id: str
    name: str
    email: str
    role: str
    token: str  # the user's own Open WebUI JWT -- never logged, never sent to the browser as JSON
    expires_at: float  # epoch seconds

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "email": self.email, "role": self.role}


class SessionCodec:
    def __init__(self, secret: str, clock=time.time):
        if not secret:
            raise ValueError("a session secret is required")
        # Any string works as the secret (e.g. `openssl rand -hex 32`).
        self._fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))
        self._clock = clock

    def encode(self, user: AuthUser) -> str:
        return self._fernet.encrypt(json.dumps(asdict(user)).encode()).decode()

    def decode(self, value: str | None) -> AuthUser | None:
        if not value:
            return None
        try:
            user = AuthUser(**json.loads(self._fernet.decrypt(value.encode())))
        except (InvalidToken, ValueError, TypeError):
            return None  # tampered, wrong secret (e.g. rotated), or malformed
        return user if user.expires_at > self._clock() else None


class LoginThrottle:
    """Per (client, email) failure limiter in front of Open WebUI's own 429,
    so a guessing loop is stopped here rather than hammering it."""

    def __init__(self, max_failures: int = 5, window_s: float = 300.0, clock=time.monotonic):
        self._max = max_failures
        self._window = window_s
        self._clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, key: str) -> deque[float]:
        failures = self._failures[key]
        cutoff = self._clock() - self._window
        while failures and failures[0] <= cutoff:
            failures.popleft()
        return failures

    def retry_after(self, key: str) -> float:
        """Seconds until another attempt is allowed (0 if allowed now)."""
        failures = self._recent(key)
        if len(failures) < self._max:
            return 0.0
        return max(0.0, failures[0] + self._window - self._clock())

    def failure(self, key: str) -> None:
        self._recent(key).append(self._clock())

    def success(self, key: str) -> None:
        self._failures.pop(key, None)

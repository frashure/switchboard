"""Sign a user in to Open WebUI with their own credentials.

Deliberately separate from OwuiClient.connect(): that one retries and keeps a
service account logged in; this is exactly one attempt per call, because a
person is waiting on a form, and retrying a wrong password only trips Open
WebUI's own rate limit (429).
"""

import base64
import json
import time
from dataclasses import dataclass

import httpx

from switchboard.config import settings

FALLBACK_LIFETIME_S = 28 * 24 * 3600  # Open WebUI's default token lifetime


@dataclass(frozen=True)
class OwuiLogin:
    token: str
    expires_at: float  # epoch seconds
    user_id: str
    name: str
    email: str
    role: str


class InvalidCredentials(Exception):
    pass


class LoginRateLimited(Exception):
    """Open WebUI itself is throttling sign-ins (HTTP 429)."""


class LoginUnavailable(Exception):
    """Open WebUI unreachable or erroring -- nothing the user can fix."""


def _jwt_expiry(token: str) -> float | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return float(exp) if exp else None
    except (IndexError, ValueError, TypeError):
        return None


async def signin(email: str, password: str, *, base_url: str | None = None) -> OwuiLogin:
    url = f"{(base_url or settings.owui_base_url).rstrip('/')}/api/v1/auths/signin"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json={"email": email, "password": password})
    except httpx.HTTPError as error:
        # str(error) can include the URL but never the request body.
        raise LoginUnavailable(type(error).__name__) from None

    if response.status_code in (400, 401, 403):
        raise InvalidCredentials()
    if response.status_code == 429:
        raise LoginRateLimited()
    if response.status_code != 200:
        raise LoginUnavailable(f"HTTP {response.status_code}")

    data = response.json()
    token = data["token"]
    expires_at = data.get("expires_at") or _jwt_expiry(token) or (time.time() + FALLBACK_LIFETIME_S)
    return OwuiLogin(
        token=token,
        expires_at=float(expires_at),
        user_id=str(data.get("id", "")),
        name=data.get("name") or data.get("email", ""),
        email=data.get("email", email),
        role=data.get("role", "user"),
    )

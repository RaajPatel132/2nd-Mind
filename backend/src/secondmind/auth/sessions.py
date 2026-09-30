"""Signed session tokens for the dev-auth cookie (HMAC-SHA256, constant-time verification).

Token: ``v1.<user_id>.<expires_unix>.<signature>``. Real sessions (revocable, server-side)
replace this in S4/S6; the format is versioned so they can coexist during the switch.
"""

import base64
import hashlib
import hmac
import time
import uuid
from collections.abc import Callable

_VERSION = "v1"
DEVICE_VERSION = "d1"


class SessionSigner:
    """Signs a user id with an expiry. ``purpose`` separates two uses of one secret: a device
    token (a returning guest) is not a session token, whatever its bytes, and each refuses the
    other's (S4.12)."""

    def __init__(
        self,
        secret: str,
        *,
        ttl_s: int = 30 * 24 * 3600,
        clock: Callable[[], float] = time.time,
        purpose: str = "",
    ) -> None:
        if len(secret) < 32:
            raise ValueError("session secret must be at least 32 characters")
        self._key = (secret if not purpose else f"{secret}|{purpose}").encode("utf-8")
        self._version = DEVICE_VERSION if purpose == "device" else _VERSION
        self._ttl_s = ttl_s
        self._clock = clock

    @property
    def ttl_s(self) -> int:
        return self._ttl_s

    def sign(self, user_id: uuid.UUID) -> str:
        expires = int(self._clock()) + self._ttl_s
        payload = f"{self._version}.{user_id}.{expires}"
        return f"{payload}.{self._mac(payload)}"

    def verify(self, token: str) -> uuid.UUID | None:
        parts = token.split(".")
        if len(parts) != 4 or parts[0] != self._version:
            return None
        payload, signature = ".".join(parts[:3]), parts[3]
        if not hmac.compare_digest(signature, self._mac(payload)):
            return None
        try:
            user_id = uuid.UUID(parts[1])
            expires = int(parts[2])
        except ValueError:
            return None
        if expires < self._clock():
            return None
        return user_id

    def _mac(self, payload: str) -> str:
        digest = hmac.new(self._key, payload.encode("utf-8"), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

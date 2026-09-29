"""Web hardening for the API (R.11): security headers on every response, a size limit on
request bodies, and a same-origin check on requests that change something.

Pure ASGI middleware, so streaming responses are untouched. There is no CORS middleware on
purpose: the SPA and the API share an origin (nginx now, CloudFront later), so a browser has no
reason to make a cross-origin request, and a preflight gets no permission headers.
"""

import json
from collections.abc import Iterable
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# JSON in, JSON out: nothing here is a page, so nothing may load or embed anything.
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
HSTS = "max-age=31536000; includeSubDomains"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Swagger UI loads its script and styles from a CDN, so the interactive docs get their own CSP.
DOCS_PATHS = ("/docs/api", "/openapi.json")


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key == name:
            return str(value.decode("latin-1"))
    return ""


async def _reply(send: Send, status: int, code: str, message: str) -> None:
    body = json.dumps({"error": {"code": code, "message": message, "request_id": None}}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class SecurityHeadersMiddleware:
    """``X-Content-Type-Options``, ``Referrer-Policy``, ``X-Frame-Options`` and a strict CSP on
    every response; ``Cache-Control: no-store`` on the API's; HSTS when served over HTTPS."""

    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self.app = app
        self._hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))

        async def send_secured(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", [])]
                have = {k.lower() for k, _ in headers}
                add: list[tuple[bytes, bytes]] = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                ]
                if path not in DOCS_PATHS:
                    add.append((b"content-security-policy", API_CSP.encode()))
                if path.startswith("/v1/"):
                    add.append((b"cache-control", b"no-store"))
                if self._hsts:
                    add.append((b"strict-transport-security", HSTS.encode()))
                headers.extend((k, v) for k, v in add if k not in have)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_secured)


class RequestGuardMiddleware:
    """Refuses, before any handler runs, what an honest client never sends:

    * a body larger than ``max_bytes`` (413), by ``Content-Length`` and while it streams in;
    * a write that isn't JSON (415), so a plain HTML form can't submit one;
    * a write a browser says came from another site (403): ``Sec-Fetch-Site`` other than
      ``same-origin``, or an ``Origin`` that isn't this site's (ADR-0016's follow-up).

    A request with neither ``Origin`` nor ``Sec-Fetch-Site`` isn't a browser's (curl, a script,
    the E2E runner): CSRF needs a browser with a cookie, so it passes on to the session check.
    """

    def __init__(
        self, app: ASGIApp, *, max_bytes: int, allowed_origins: Iterable[str] = ()
    ) -> None:
        self.app = app
        self._max = max_bytes
        self._origins = frozenset(o.rstrip("/").lower() for o in allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method", "GET")).upper()
        length = _header(scope, b"content-length")
        if length.isdigit() and int(length) > self._max:
            await _reply(send, 413, "payload_too_large", "That request is too large.")
            return
        if method in UNSAFE_METHODS:
            refusal = self._refusal(scope, has_body=length != "0")
            if refusal is not None:
                status, code, message = refusal
                await _reply(send, status, code, message)
                return
        if length == "" and method in UNSAFE_METHODS:
            # No declared size (chunked): read it here, up to the limit, and hand it on.
            body = await self._read(receive)
            if body is None:
                await _reply(send, 413, "payload_too_large", "That request is too large.")
                return
            await self.app(scope, _replay(body), send)
            return
        await self.app(scope, receive, send)

    async def _read(self, receive: Receive) -> bytes | None:
        """The whole body, or None if it goes past the limit."""
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return b"".join(chunks)
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self._max:
                return None
            chunks.append(chunk)
            if not message.get("more_body", False):
                return b"".join(chunks)

    def _refusal(self, scope: Scope, *, has_body: bool) -> tuple[int, str, str] | None:
        site = _header(scope, b"sec-fetch-site").lower()
        if site and site != "same-origin":
            return 403, "cross_site_request", "Requests from another site aren't accepted."
        origin = _header(scope, b"origin")
        if origin and not self._same_origin(scope, origin):
            return 403, "cross_site_request", "Requests from another site aren't accepted."
        if has_body and "application/json" not in _header(scope, b"content-type").lower():
            return 415, "unsupported_media_type", "Send JSON with Content-Type: application/json."
        return None

    def _same_origin(self, scope: Scope, origin: str) -> bool:
        if origin.lower().rstrip("/") in self._origins:
            return True
        host = (_header(scope, b"x-forwarded-host") or _header(scope, b"host")).lower()
        parts = urlsplit(origin)
        return bool(host) and parts.netloc.lower() == host


def _replay(body: bytes) -> Receive:
    """A ``receive`` that hands the app a body already read."""
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive

"""Request id middleware (pure ASGI, so streaming responses are untouched)."""

import re
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from secondmind.observability import bind_log_context, clear_log_context

HEADER = "x-request-id"
_VALID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class RequestIdMiddleware:
    """Accepts a well-formed inbound X-Request-ID or mints one; binds it to the log context
    (turn tasks started by the request inherit it) and echoes it on the response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inbound = dict(scope.get("headers", [])).get(HEADER.encode(), b"").decode("latin-1")
        request_id = inbound if _VALID.match(inbound) else uuid.uuid4().hex
        clear_log_context()
        bind_log_context(request_id=request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((HEADER.encode(), request_id.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_id)

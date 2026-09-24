"""Server-sent events for a running turn: turn.started, token*, turn.completed | turn.failed."""

from collections.abc import AsyncIterator, Callable

from pydantic import BaseModel

from secondmind.agent import (
    TokenDelta,
    Turn,
    TurnCompleted,
    TurnFailed,
    TurnHandle,
    TurnStarted,
)
from secondmind.api.schemas import (
    SseToken,
    SseTurnCompleted,
    SseTurnFailed,
    SseTurnStarted,
    TurnError,
    TurnOut,
)

KEEPALIVE = b": keep-alive\n\n"


def frame(event: str, data: BaseModel) -> bytes:
    return f"event: {event}\ndata: {data.model_dump_json()}\n\n".encode()


async def turn_stream(
    handle: TurnHandle, to_out: Callable[[Turn], TurnOut], *, keepalive_s: float = 10.0
) -> AsyncIterator[bytes]:
    """Relay the turn's events. Keep-alive comments stop proxies closing a quiet stream."""
    while True:
        try:
            event = await handle.next_event(keepalive_s)
        except TimeoutError:
            yield KEEPALIVE
            continue
        match event:
            case None:
                return
            case TurnStarted(turn=turn):
                yield frame(
                    "turn.started",
                    SseTurnStarted(
                        turn_id=turn.id, workspace_id=turn.workspace_id, started_at=turn.started_at
                    ),
                )
            case TokenDelta(text=text):
                yield frame("token", SseToken(text=text))
            case TurnCompleted(turn=turn):
                yield frame(
                    "turn.completed",
                    SseTurnCompleted(turn_id=turn.id, usage=turn.usage, turn=to_out(turn)),
                )
            case TurnFailed(turn=turn):
                error = TurnError(
                    code=turn.error_code or "internal_error", message=turn.error_message or ""
                )
                yield frame(
                    "turn.failed",
                    SseTurnFailed(
                        turn_id=turn.id, error=error, usage=turn.usage, turn=to_out(turn)
                    ),
                )

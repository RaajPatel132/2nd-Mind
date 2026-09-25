"""Turns: send a message (streamed reply over SSE), page history, read a turn and its events."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from secondmind.agent import Turn
from secondmind.api.deps import ServicesDep, UserIdDep, find_turn
from secondmind.api.errors import ERROR_RESPONSES
from secondmind.api.schemas import (
    CreateTurnIn,
    TurnEventOut,
    TurnEventsOut,
    TurnOut,
    TurnPage,
    UsageOut,
)
from secondmind.api.services import Services
from secondmind.api.sse import turn_stream
from secondmind.auth import resolve_scope

router = APIRouter(prefix="/v1", tags=["turns"])

SSE_DESCRIPTION = (
    "A `text/event-stream` of frames: `turn.started`, then `token`, `step.started` and "
    "`turn.event` (each repeated, in the order they happened), then exactly one of "
    "`turn.completed` or `turn.failed`. Comment lines are keep-alives. Clients may ignore "
    "`step.started` and `turn.event`."
)


async def usage_for(services: Services, user_id: uuid.UUID) -> UsageOut:
    workspaces = await services.identity.workspaces_for(user_id)
    usage = await services.quotas.usage(user_id, [w.id for w in workspaces])
    return UsageOut.of(usage)


def turn_out(services: Services, turn: Turn) -> TurnOut:
    return TurnOut.of(turn, services.tracer.trace_url(turn.id))


@router.post(
    "/workspaces/{workspace_id}/turns",
    response_class=StreamingResponse,
    responses={200: {"description": SSE_DESCRIPTION}, **ERROR_RESPONSES},
)
async def create_turn(
    workspace_id: uuid.UUID, body: CreateTurnIn, services: ServicesDep, user_id: UserIdDep
) -> StreamingResponse:
    """Send a message; the reply streams back as server-sent events."""
    scope, workspace = await resolve_scope(
        services.identity, user_id=user_id, workspace_id=workspace_id
    )
    handle = await services.runner.start(
        scope,
        text=body.message,
        timezone=workspace.timezone,
        default_lead_minutes=workspace.default_lead_minutes,
        model=body.model,
    )

    async def quota_after() -> UsageOut:
        return await usage_for(services, user_id)

    return StreamingResponse(
        turn_stream(handle, lambda t: turn_out(services, t), quota_after=quota_after),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Turn-Id": str(handle.turn.id),
        },
    )


@router.get(
    "/workspaces/{workspace_id}/turns",
    response_model=TurnPage,
    responses=ERROR_RESPONSES,
)
async def list_turns(
    workspace_id: uuid.UUID,
    services: ServicesDep,
    user_id: UserIdDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before: Annotated[uuid.UUID | None, Query(description="Turn id to page from")] = None,
) -> TurnPage:
    """Conversation history for a workspace, newest first (FR-1.6)."""
    scope, _ = await resolve_scope(services.identity, user_id=user_id, workspace_id=workspace_id)
    turns = await services.runner.store(scope).recent(limit=limit + 1, before=before)
    page, more = turns[:limit], len(turns) > limit
    return TurnPage(
        items=[turn_out(services, t) for t in page],
        next_before=page[-1].id if more and page else None,
    )


@router.get("/turns/{turn_id}", response_model=TurnOut, responses=ERROR_RESPONSES)
async def get_turn(turn_id: uuid.UUID, services: ServicesDep, user_id: UserIdDep) -> TurnOut:
    turn, _ = await find_turn(services, user_id, turn_id)
    return turn_out(services, turn)


@router.get(
    "/turns/{turn_id}/events",
    response_model=TurnEventsOut,
    responses=ERROR_RESPONSES,
)
async def get_turn_events(
    turn_id: uuid.UUID, services: ServicesDep, user_id: UserIdDep
) -> TurnEventsOut:
    """The turn's structured events, in order: the glass box's source of truth (FR-9.2)."""
    turn, store = await find_turn(services, user_id, turn_id)
    events = await store.events(turn.id)
    return TurnEventsOut(turn_id=turn.id, events=[TurnEventOut.of(e) for e in events])

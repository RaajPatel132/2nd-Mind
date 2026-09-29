"""Turns: send a message (streamed reply over SSE), page history, read a turn and its events."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from secondmind.agent import Turn
from secondmind.api.deps import ServicesDep, UserIdDep, find_turn
from secondmind.api.errors import ERROR_RESPONSES, RATE_LIMITED_RESPONSE
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
from secondmind.core import (
    QuotaEvent,
    RateLimitedError,
    Tier,
    UnauthenticatedError,
    ValidationFailedError,
)
from secondmind.metering import Block, QuotaUsage

router = APIRouter(prefix="/v1", tags=["turns"])

SSE_DESCRIPTION = (
    "A `text/event-stream` of frames: `turn.started`, then `token`, `step.started` and "
    "`turn.event` (each repeated, in the order they happened), then exactly one of "
    "`turn.completed` or `turn.failed`. Comment lines are keep-alives. Clients may ignore "
    "`step.started` and `turn.event`."
)


async def usage_for(services: Services, user_id: uuid.UUID) -> UsageOut:
    usage, block = await usage_and_block(services, user_id)
    return UsageOut.of(usage, block)


async def usage_and_block(
    services: Services, user_id: uuid.UUID
) -> tuple[QuotaUsage, Block | None]:
    """The person's quota, and what stops their next turn (the app's blocks, then the quota)."""
    user = await services.identity.get_user(user_id)
    if user is None:
        raise UnauthenticatedError("Sign in first.")
    workspaces = await services.identity.workspaces_for(user_id)
    usage = await services.quotas.usage(user_id, [w.id for w in workspaces], user.tier)
    return usage, await services.gate.turn_block(usage)


def _may_pick(services: Services, tier: Tier, model: str) -> bool:
    """A pick must be one this tier is offered; anything else is refused before a turn starts."""
    return any(
        str(choice.ref) == model and tier in choice.tiers
        for choice in services.config.routing.choices
    )


def turn_out(services: Services, turn: Turn) -> TurnOut:
    return TurnOut.of(turn, services.tracer.trace_url(turn.id))


@router.post(
    "/workspaces/{workspace_id}/turns",
    response_class=StreamingResponse,
    responses={200: {"description": SSE_DESCRIPTION}, **ERROR_RESPONSES, **RATE_LIMITED_RESPONSE},
)
async def create_turn(
    workspace_id: uuid.UUID, body: CreateTurnIn, services: ServicesDep, user_id: UserIdDep
) -> StreamingResponse:
    """Send a message; the reply streams back as server-sent events."""
    scope, workspace = await resolve_scope(
        services.identity, user_id=user_id, workspace_id=workspace_id
    )
    settings = services.config.settings
    wait = await services.gate.rate_limited(str(user_id), settings.rate_turns_per_minute)
    if wait is not None:
        raise RateLimitedError("You're sending messages too fast. Try again in a moment.", wait)
    usage, block = await usage_and_block(services, user_id)
    if body.model is not None and not _may_pick(services, usage.tier, body.model):
        raise ValidationFailedError("That model isn't available on your plan.")

    async def stored_quota() -> QuotaEvent:
        usage = await usage_for(services, user_id)
        return QuotaEvent(
            limit_usd=float(usage.limit_usd),
            used_usd=float(usage.used_usd),
            remaining_usd=float(usage.remaining_usd),
        )

    handle = await services.runner.start(
        scope,
        text=body.message,
        timezone=workspace.timezone,
        default_lead_minutes=workspace.default_lead_minutes,
        model=body.model,
        block=block,
        quota_after=stored_quota,
    )

    async def quota_after() -> UsageOut:
        return await usage_for(services, user_id)

    return StreamingResponse(
        turn_stream(
            handle,
            lambda t: turn_out(services, t),
            quota_after=quota_after,
            keepalive_s=settings.sse_heartbeat_s,
        ),
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

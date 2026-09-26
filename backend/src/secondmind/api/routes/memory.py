"""Memory actions from the glass box: undo a turn (S2.12), confirm or reject a held write
(S2.3), edit a memory in place (S3.12), and plain detail views of an item or entity (S2.11)."""

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from secondmind.api.deps import ServicesDep, UserIdDep, find_turn
from secondmind.api.errors import ERROR_RESPONSES
from secondmind.api.routes.turns import turn_out
from secondmind.api.schemas import (
    EntityDetailOut,
    HeldWriteOut,
    HeldWritesOut,
    ItemDetailOut,
    ItemEditIn,
    TurnOut,
)
from secondmind.api.services import Services
from secondmind.auth import Workspace, resolve_scope
from secondmind.core import NotFoundError, WorkspaceScope
from secondmind.memory import MemoryReader

router = APIRouter(prefix="/v1", tags=["memory"])


async def _find[T](
    services: Services,
    user_id: uuid.UUID,
    lookup: Callable[[MemoryReader], Awaitable[T | None]],
    what: str,
) -> tuple[T, WorkspaceScope, Workspace]:
    """Find a row in any of the user's workspaces (each lookup is RLS-scoped)."""
    for workspace in await services.identity.workspaces_for(user_id):
        scope = WorkspaceScope(workspace_id=workspace.id, user_id=user_id)
        found = await lookup(services.runner.memory.reader(scope))
        if found is not None:
            return found, scope, workspace
    raise NotFoundError(f"{what} not found")


@router.post("/turns/{turn_id}/undo", response_model=TurnOut, responses=ERROR_RESPONSES)
async def undo_turn(turn_id: uuid.UUID, services: ServicesDep, user_id: UserIdDep) -> TurnOut:
    """Revert every memory write the turn made, as a new turn with its own diff (FR-10.1).
    Undoing an undo turn is a redo."""
    turn, _ = await find_turn(services, user_id, turn_id)
    scope, workspace = await resolve_scope(
        services.identity, user_id=user_id, workspace_id=turn.workspace_id
    )
    undo = await services.runner.undo(scope, turn_id=turn.id, timezone=workspace.timezone)
    return turn_out(services, undo)


@router.get(
    "/workspaces/{workspace_id}/held-writes",
    response_model=HeldWritesOut,
    responses=ERROR_RESPONSES,
)
async def list_held_writes(
    workspace_id: uuid.UUID,
    services: ServicesDep,
    user_id: UserIdDep,
    status: Annotated[Literal["pending", "confirmed", "rejected"] | None, Query()] = "pending",
) -> HeldWritesOut:
    scope, _ = await resolve_scope(services.identity, user_id=user_id, workspace_id=workspace_id)
    held = await services.runner.memory.reader(scope).held_writes(status=status)
    return HeldWritesOut(items=[HeldWriteOut.of(h) for h in held])


@router.post("/held-writes/{held_id}/confirm", response_model=TurnOut, responses=ERROR_RESPONSES)
async def confirm_held_write(
    held_id: uuid.UUID, services: ServicesDep, user_id: UserIdDep
) -> TurnOut:
    """Apply a held write as a new turn (with its own diff, so undo still works)."""
    _, scope, workspace = await _find(
        services, user_id, lambda r: r.held_write(held_id), "held write"
    )
    turn = await services.runner.confirm_held(scope, held_id=held_id, timezone=workspace.timezone)
    return turn_out(services, turn)


@router.post(
    "/held-writes/{held_id}/reject", response_model=HeldWriteOut, responses=ERROR_RESPONSES
)
async def reject_held_write(
    held_id: uuid.UUID, services: ServicesDep, user_id: UserIdDep
) -> HeldWriteOut:
    _, scope, _ = await _find(services, user_id, lambda r: r.held_write(held_id), "held write")
    held = await services.runner.memory.reject_held(scope, held_id, at=services.clock())
    return HeldWriteOut.of(held)


@router.get("/items/{item_id}", response_model=ItemDetailOut, responses=ERROR_RESPONSES)
async def get_item(item_id: uuid.UUID, services: ServicesDep, user_id: UserIdDep) -> ItemDetailOut:
    item, scope, _ = await _find(services, user_id, lambda r: r.item(item_id), "item")
    reader = services.runner.memory.reader(scope)
    return ItemDetailOut(
        item=item,
        entities=await reader.item_entities([item_id]),
        links=await reader.links([item_id]),
        triggers=await reader.triggers([item_id]),
    )


@router.patch("/items/{item_id}", response_model=TurnOut, responses=ERROR_RESPONSES)
async def edit_item(
    item_id: uuid.UUID, body: ItemEditIn, services: ServicesDep, user_id: UserIdDep
) -> TurnOut:
    """Edit one memory in place, as its own turn (source ``ui_edit``) through the writer and
    policy, with its own glass box; undo reverses it (FR-10.3). A date is free text read by
    the resolver ("Friday", "3 October"); a delete is held for confirmation."""
    _, scope, workspace = await _find(services, user_id, lambda r: r.item(item_id), "item")
    turn = await services.runner.edit_item(
        scope,
        item_id=item_id,
        changes=body.changes(),
        delete=body.delete,
        timezone=workspace.timezone,
    )
    return turn_out(services, turn)


@router.get("/entities/{entity_id}", response_model=EntityDetailOut, responses=ERROR_RESPONSES)
async def get_entity(
    entity_id: uuid.UUID, services: ServicesDep, user_id: UserIdDep
) -> EntityDetailOut:
    entity, scope, _ = await _find(services, user_id, lambda r: r.entity(entity_id), "entity")
    items = await services.runner.memory.reader(scope).entity_items(entity_id)
    return EntityDetailOut(entity=entity, item_ids=items)

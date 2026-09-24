"""Request dependencies: services, the signed-in user, and workspace scope resolution."""

import uuid
from typing import Annotated

from fastapi import Depends, Request

from secondmind.agent import Turn, TurnStore
from secondmind.api.services import Services
from secondmind.core import NotFoundError, UnauthenticatedError, WorkspaceScope

SESSION_COOKIE = "sm_session"


def get_services(request: Request) -> Services:
    services: Services | None = getattr(request.app.state, "services", None)
    if services is None:
        raise RuntimeError("services are not initialised")
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


async def current_user_id(request: Request, services: ServicesDep) -> uuid.UUID:
    token = request.cookies.get(SESSION_COOKIE)
    user_id = services.signer.verify(token) if token else None
    if user_id is None or await services.identity.get_user(user_id) is None:
        raise UnauthenticatedError("Sign in first.")
    return user_id


UserIdDep = Annotated[uuid.UUID, Depends(current_user_id)]


async def find_turn(
    services: Services, user_id: uuid.UUID, turn_id: uuid.UUID
) -> tuple[Turn, TurnStore]:
    """Find a turn in any of the user's workspaces (each lookup is RLS-scoped)."""
    for workspace in await services.identity.workspaces_for(user_id):
        store = services.runner.store(WorkspaceScope(workspace_id=workspace.id, user_id=user_id))
        turn = await store.get(turn_id)
        if turn is not None:
            return turn, store
    raise NotFoundError("turn not found")

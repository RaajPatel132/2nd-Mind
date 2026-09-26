"""Dev-only helpers (DEV_AUTH=true, never in production): seed the synthetic recall fixture
into the signed-in user's workspace, for local exploration and the E2E suite (S3.1)."""

from fastapi import APIRouter

from secondmind.api.deps import ServicesDep, UserIdDep
from secondmind.api.errors import ERROR_RESPONSES
from secondmind.api.schemas import DevSeedOut
from secondmind.auth import resolve_scope
from secondmind.core import NotFoundError

router = APIRouter(prefix="/v1/dev", tags=["dev"])


@router.post("/seed-recall", response_model=DevSeedOut, responses=ERROR_RESPONSES)
async def seed_recall(services: ServicesDep, user_id: UserIdDep) -> DevSeedOut:
    """Write the recall fixture into the user's private workspace, once (a second call finds
    it there and does nothing)."""
    if services.seed_recall is None:
        raise NotFoundError("not found")
    workspaces = await services.identity.workspaces_for(user_id)
    if not workspaces:
        raise NotFoundError("no workspace")
    scope, _ = await resolve_scope(
        services.identity, user_id=user_id, workspace_id=workspaces[0].id
    )
    items = await services.seed_recall(scope)
    return DevSeedOut(seeded=items > 0, items=items)

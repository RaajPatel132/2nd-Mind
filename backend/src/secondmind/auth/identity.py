"""Users, workspaces and the rule that turns a request into a WorkspaceScope."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from secondmind.core import NotFoundError, WorkspaceScope


class WorkspaceKind(StrEnum):
    PRIVATE = "private"
    GUEST = "guest"
    PERSONA_COPY = "persona_copy"


class User(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID
    email: str | None
    created_at: datetime


class Workspace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID
    owner_user_id: uuid.UUID
    kind: WorkspaceKind
    timezone: str
    created_at: datetime
    default_lead_minutes: int = 1440


class IdentityStore(Protocol):
    async def ensure_user_with_private_workspace(
        self, *, email: str, timezone: str
    ) -> tuple[User, Workspace]:
        """Create (or reuse) the user for ``email`` and their one private workspace."""
        ...

    async def get_user(self, user_id: uuid.UUID) -> User | None: ...

    async def get_workspace(self, workspace_id: uuid.UUID) -> Workspace | None: ...

    async def workspaces_for(self, user_id: uuid.UUID) -> list[Workspace]: ...


async def resolve_scope(
    identity: IdentityStore, *, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> tuple[WorkspaceScope, Workspace]:
    """The acting user may only enter workspaces they own. Anything else is 'not found', so
    the existence of other people's workspaces is never revealed."""
    workspace = await identity.get_workspace(workspace_id)
    if workspace is None or workspace.owner_user_id != user_id:
        raise NotFoundError("workspace not found")
    return WorkspaceScope(workspace_id=workspace.id, user_id=user_id), workspace

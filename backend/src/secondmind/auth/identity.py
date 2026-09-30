"""Users, workspaces and the rule that turns a request into a WorkspaceScope."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from secondmind.core import NotFoundError, Tier, WorkspaceScope


class WorkspaceKind(StrEnum):
    PRIVATE = "private"
    GUEST = "guest"
    PERSONA_COPY = "persona_copy"
    # The canonical persona (S4.10): owned by a system user, copied for each visitor, never entered.
    TEMPLATE = "template"
    # A guest's own empty memory (S4.12).
    SCRATCH = "scratch"


class User(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID
    email: str | None
    created_at: datetime
    # What the person may spend and pick (ADR-0032); set by the admin CLI, never by the app.
    tier: Tier = Tier.STANDARD


class TierChange(BaseModel):
    """One audited change of a person's tier: who made it, when, from what to what."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: uuid.UUID
    from_tier: Tier
    to_tier: Tier
    changed_by: str
    changed_at: datetime


class Workspace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID
    owner_user_id: uuid.UUID
    kind: WorkspaceKind
    timezone: str
    created_at: datetime
    default_lead_minutes: int = 1440
    # A copy of the persona records which seed it came from and how far it was moved (S4.11).
    seed_id: str | None = None
    seed_version: int | None = None
    seed_hash: str | None = None
    moved_days: int | None = None


class IdentityStore(Protocol):
    async def ensure_user_with_private_workspace(
        self, *, email: str, timezone: str
    ) -> tuple[User, Workspace]:
        """Create (or reuse) the user for ``email`` and their one private workspace."""
        ...

    async def get_user(self, user_id: uuid.UUID) -> User | None: ...

    async def get_workspace(self, workspace_id: uuid.UUID) -> Workspace | None: ...

    async def workspaces_for(self, user_id: uuid.UUID) -> list[Workspace]: ...

    async def all_workspaces(self) -> list[Workspace]:
        """Every workspace a person can use, oldest first (background jobs that sweep all of them).
        A persona template is left out: nothing runs against it but the seed and the copy."""
        ...

    async def create_user(self, *, email: str | None, tier: Tier = Tier.STANDARD) -> User:
        """A new user. A guest has no email, and neither has the system user owning a template."""
        ...

    async def create_workspace(
        self, *, owner_user_id: uuid.UUID, kind: WorkspaceKind, timezone: str
    ) -> Workspace: ...

    async def delete_workspace(self, workspace_id: uuid.UUID) -> None:
        """Remove a workspace and everything in it (cascades to every workspace-owned table)."""
        ...

    async def delete_user(self, user_id: uuid.UUID) -> None:
        """Remove a user and their workspaces (a guest whose sample couldn't be made)."""
        ...

    async def expire_guests(self, before: datetime) -> int:
        """Empty the workspaces of guests created before ``before`` (S4.12); how many workspaces.
        The usage ledger (costs, no content) is kept."""
        ...

    async def template_for(self, seed_id: str) -> Workspace | None:
        """The template workspace of a seed, if it has been loaded."""
        ...


async def resolve_scope(
    identity: IdentityStore, *, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> tuple[WorkspaceScope, Workspace]:
    """The acting user may only enter workspaces they own. Anything else is 'not found', so
    the existence of other people's workspaces is never revealed."""
    workspace = await identity.get_workspace(workspace_id)
    if (
        workspace is None
        or workspace.owner_user_id != user_id
        or workspace.kind is WorkspaceKind.TEMPLATE  # never entered, by anyone (S4.10)
    ):
        raise NotFoundError("workspace not found")
    return WorkspaceScope(workspace_id=workspace.id, user_id=user_id), workspace

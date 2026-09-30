"""In-memory test doubles for ports (no database)."""

import uuid

from secondmind.agent.adapters.inmemory import InMemoryTurns, InMemoryTurnStore
from secondmind.core import utc_now
from secondmind.metering.adapters import InMemorySpendStore

__all__ = ["InMemoryIdentity", "InMemorySpendStore", "InMemoryTurnStore", "InMemoryTurns"]


class InMemoryIdentity:
    """IdentityStore double."""

    def __init__(self) -> None:
        from secondmind.auth import User, Workspace  # noqa: PLC0415 - keep module import light

        self.users: dict[uuid.UUID, User] = {}
        self.workspaces: dict[uuid.UUID, Workspace] = {}

    async def ensure_user_with_private_workspace(self, *, email: str, timezone: str):  # type: ignore[no-untyped-def]
        from secondmind.auth import User, Workspace, WorkspaceKind  # noqa: PLC0415
        from secondmind.core import new_id  # noqa: PLC0415

        user = next((u for u in self.users.values() if u.email == email), None)
        if user is None:
            user = User(id=new_id(), email=email, created_at=utc_now())
            self.users[user.id] = user
        ws = next(
            (
                w
                for w in self.workspaces.values()
                if w.owner_user_id == user.id and w.kind == "private"
            ),
            None,
        )
        if ws is None:
            ws = Workspace(
                id=new_id(),
                owner_user_id=user.id,
                kind=WorkspaceKind.PRIVATE,
                timezone=timezone,
                created_at=utc_now(),
            )
            self.workspaces[ws.id] = ws
        return user, ws

    async def get_user(self, user_id: uuid.UUID):  # type: ignore[no-untyped-def]
        return self.users.get(user_id)

    async def get_workspace(self, workspace_id: uuid.UUID):  # type: ignore[no-untyped-def]
        return self.workspaces.get(workspace_id)

    async def workspaces_for(self, user_id: uuid.UUID):  # type: ignore[no-untyped-def]
        return [w for w in self.workspaces.values() if w.owner_user_id == user_id]

    async def all_workspaces(self):  # type: ignore[no-untyped-def]
        return [w for w in self.workspaces.values() if w.kind != "template"]

    async def create_user(self, *, email, tier=None):  # type: ignore[no-untyped-def]
        from secondmind.auth import User  # noqa: PLC0415
        from secondmind.core import Tier, new_id  # noqa: PLC0415

        user = User(id=new_id(), email=email, created_at=utc_now(), tier=tier or Tier.STANDARD)
        self.users[user.id] = user
        return user

    async def create_workspace(self, *, owner_user_id, kind, timezone):  # type: ignore[no-untyped-def]
        from secondmind.auth import Workspace  # noqa: PLC0415
        from secondmind.core import new_id  # noqa: PLC0415

        ws = Workspace(
            id=new_id(),
            owner_user_id=owner_user_id,
            kind=kind,
            timezone=timezone,
            created_at=utc_now(),
        )
        self.workspaces[ws.id] = ws
        return ws

    async def delete_workspace(self, workspace_id):  # type: ignore[no-untyped-def]
        self.workspaces.pop(workspace_id, None)

    async def delete_user(self, user_id):  # type: ignore[no-untyped-def]
        self.users.pop(user_id, None)
        for ws_id in [w.id for w in self.workspaces.values() if w.owner_user_id == user_id]:
            self.workspaces.pop(ws_id)

    async def expire_guests(self, before):  # type: ignore[no-untyped-def]
        return 0

    async def template_for(self, seed_id):  # type: ignore[no-untyped-def]
        return next(
            (w for w in self.workspaces.values() if w.kind == "template" and w.seed_id == seed_id),
            None,
        )

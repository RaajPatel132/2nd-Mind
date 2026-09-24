"""SQL identity store."""

import uuid

from sqlalchemy import select, text

from secondmind.auth import User, Workspace, WorkspaceKind
from secondmind.auth.adapters.tables import UserRow, WorkspaceRow
from secondmind.core import new_id
from secondmind.memory.adapters import Database


class SqlIdentityStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def ensure_user_with_private_workspace(
        self, *, email: str, timezone: str
    ) -> tuple[User, Workspace]:
        async with self._db.identity() as session:
            await session.execute(
                text(
                    "INSERT INTO users (id, email) VALUES (:id, :email) "
                    "ON CONFLICT (lower(email)) DO NOTHING"
                ),
                {"id": new_id(), "email": email},
            )
            user = (
                await session.execute(
                    select(UserRow).where(text("lower(email) = lower(:email)")),
                    {"email": email},
                )
            ).scalar_one()
            await session.execute(
                text(
                    "INSERT INTO workspaces (id, owner_user_id, kind, timezone) "
                    "VALUES (:id, :owner, 'private', :tz) "
                    "ON CONFLICT (owner_user_id) WHERE kind = 'private' DO NOTHING"
                ),
                {"id": new_id(), "owner": user.id, "tz": timezone},
            )
            workspace = (
                await session.execute(
                    select(WorkspaceRow).where(
                        WorkspaceRow.owner_user_id == user.id,
                        WorkspaceRow.kind == WorkspaceKind.PRIVATE.value,
                    )
                )
            ).scalar_one()
            return _user(user), _workspace(workspace)

    async def get_user(self, user_id: uuid.UUID) -> User | None:
        async with self._db.identity() as session:
            row = await session.get(UserRow, user_id)
            return None if row is None else _user(row)

    async def get_workspace(self, workspace_id: uuid.UUID) -> Workspace | None:
        async with self._db.identity() as session:
            row = await session.get(WorkspaceRow, workspace_id)
            return None if row is None else _workspace(row)

    async def workspaces_for(self, user_id: uuid.UUID) -> list[Workspace]:
        async with self._db.identity() as session:
            rows = (
                await session.execute(
                    select(WorkspaceRow)
                    .where(WorkspaceRow.owner_user_id == user_id)
                    .order_by(WorkspaceRow.created_at)
                )
            ).scalars()
            return [_workspace(r) for r in rows]


def _user(row: UserRow) -> User:
    return User(id=row.id, email=row.email, created_at=row.created_at)


def _workspace(row: WorkspaceRow) -> Workspace:
    return Workspace(
        id=row.id,
        owner_user_id=row.owner_user_id,
        kind=WorkspaceKind(row.kind),
        timezone=row.timezone,
        created_at=row.created_at,
    )

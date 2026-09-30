"""SQL identity store."""

import uuid
from datetime import datetime

from sqlalchemy import delete, select, text

from secondmind.auth import User, Workspace, WorkspaceKind
from secondmind.auth.adapters.tables import UserRow, WorkspaceRow
from secondmind.core import Tier, new_id
from secondmind.memory.adapters import Database

# What ``expire_guest_workspaces`` (migration 0008) empties: every workspace-owned table with
# content. The usage ledger is kept on purpose (costs only, no content) so the caps and the totals
# stay right. A catalog test fails when a new workspace-owned table is in neither set (S4.12).
GUEST_EMPTIED = frozenset(
    {
        "categories",
        "conversation_keys",
        "entities",  # all but the workspace's own "me", which is cleared
        "entity_relations",
        "held_writes",
        "item_access",
        "item_versions",
        "link_sources",
        "memory_entities",
        "memory_items",
        "memory_keys",
        "memory_links",
        "triggers",
        "turn_events",
        "turns",
        "vocab_terms",
        "write_log",
    }
)
GUEST_KEPT = frozenset({"usage_ledger"})


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
                    .where(
                        WorkspaceRow.owner_user_id == user_id,
                        WorkspaceRow.kind != WorkspaceKind.TEMPLATE.value,
                        WorkspaceRow.expired_at.is_(None),
                    )
                    .order_by(WorkspaceRow.created_at)
                )
            ).scalars()
            return [_workspace(r) for r in rows]

    async def all_workspaces(self) -> list[Workspace]:
        async with self._db.identity() as session:
            rows = (
                await session.execute(
                    select(WorkspaceRow)
                    .where(
                        WorkspaceRow.kind != WorkspaceKind.TEMPLATE.value,
                        WorkspaceRow.expired_at.is_(None),
                    )
                    .order_by(WorkspaceRow.created_at)
                )
            ).scalars()
            return [_workspace(r) for r in rows]

    async def create_user(self, *, email: str | None, tier: Tier = Tier.STANDARD) -> User:
        async with self._db.identity() as session:
            row = UserRow(id=new_id(), email=email, tier=tier.value)
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return _user(row)

    async def create_workspace(
        self, *, owner_user_id: uuid.UUID, kind: WorkspaceKind, timezone: str
    ) -> Workspace:
        async with self._db.identity() as session:
            row = WorkspaceRow(
                id=new_id(), owner_user_id=owner_user_id, kind=kind.value, timezone=timezone
            )
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return _workspace(row)

    async def delete_workspace(self, workspace_id: uuid.UUID) -> None:
        async with self._db.identity() as session:
            await session.execute(delete(WorkspaceRow).where(WorkspaceRow.id == workspace_id))

    async def delete_user(self, user_id: uuid.UUID) -> None:
        async with self._db.identity() as session:
            await session.execute(delete(UserRow).where(UserRow.id == user_id))

    async def expire_guests(self, before: datetime) -> int:
        async with self._db.identity() as session:
            count = (
                await session.execute(
                    text("SELECT expire_guest_workspaces(:before)"), {"before": before}
                )
            ).scalar_one()
            return int(count)

    async def template_for(self, seed_id: str) -> Workspace | None:
        async with self._db.identity() as session:
            row = (
                await session.execute(
                    select(WorkspaceRow).where(
                        WorkspaceRow.kind == WorkspaceKind.TEMPLATE.value,
                        WorkspaceRow.seed_id == seed_id,
                    )
                )
            ).scalar_one_or_none()
            return None if row is None else _workspace(row)


def _user(row: UserRow) -> User:
    return User(id=row.id, email=row.email, created_at=row.created_at, tier=Tier(row.tier))


def _workspace(row: WorkspaceRow) -> Workspace:
    return Workspace(
        id=row.id,
        owner_user_id=row.owner_user_id,
        kind=WorkspaceKind(row.kind),
        timezone=row.timezone,
        created_at=row.created_at,
        default_lead_minutes=row.default_lead_minutes,
        seed_id=row.seed_id,
        seed_version=row.seed_version,
        seed_hash=row.seed_hash,
        moved_days=row.moved_days,
    )

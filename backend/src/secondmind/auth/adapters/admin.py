"""Admin operations on identity, run by the admin CLI as the schema owner (ADR-0032).

The app's own role can read ``tier_changes`` and set nothing on it; this store is given the
owner's connection, so a tier changes only through here, and every change is audited."""

from sqlalchemy import select, text

from secondmind.auth import TierChange
from secondmind.auth.adapters.tables import TierChangeRow, UserRow
from secondmind.core import NotFoundError, Tier
from secondmind.memory.adapters import Database


class SqlAdminStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def set_tier(self, *, email: str, tier: Tier, changed_by: str) -> TierChange:
        """Move the person with this email to ``tier``, writing the audit row in the same
        transaction. Raises :class:`NotFoundError` if nobody has signed in with that email."""
        async with self._db.identity() as session:
            user = (
                await session.execute(
                    select(UserRow).where(text("lower(email) = lower(:email)")).with_for_update(),
                    {"email": email},
                )
            ).scalar_one_or_none()
            if user is None:
                raise NotFoundError(f"no user with the email {email!r}")
            before = user.tier
            user.tier = tier.value
            change = TierChangeRow(
                user_id=user.id, from_tier=before, to_tier=tier.value, changed_by=changed_by
            )
            session.add(change)
            await session.flush()
            await session.refresh(change)
            return _change(change)

    async def tier_changes(self, email: str) -> list[TierChange]:
        async with self._db.identity() as session:
            rows = (
                await session.execute(
                    select(TierChangeRow)
                    .join(UserRow, UserRow.id == TierChangeRow.user_id)
                    .where(text("lower(users.email) = lower(:email)"))
                    .order_by(TierChangeRow.id),
                    {"email": email},
                )
            ).scalars()
            return [_change(r) for r in rows]


def _change(row: TierChangeRow) -> TierChange:
    return TierChange(
        user_id=row.user_id,
        from_tier=Tier(row.from_tier),
        to_tier=Tier(row.to_tier),
        changed_by=row.changed_by,
        changed_at=row.changed_at,
    )

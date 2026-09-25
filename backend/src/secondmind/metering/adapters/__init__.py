"""SQL usage ledger."""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from secondmind.core import WorkspaceScope, new_id
from secondmind.memory.adapters import Database
from secondmind.metering import LedgerEntry
from secondmind.metering.adapters.tables import UsageLedgerRow

_INSERT = text(
    """
    INSERT INTO usage_ledger (
        id, workspace_id, owner_user_id, turn_id, step, provider, model,
        input_tokens, cached_input_tokens, output_tokens, cost_usd, charged_tokens,
        price_version
    )
    SELECT :id, w.id, w.owner_user_id, :turn_id, :step, :provider, :model,
           :input_tokens, :cached_input_tokens, :output_tokens, :cost_usd, :charged_tokens,
           :price_version
    FROM workspaces w WHERE w.id = :workspace_id
    """
)


async def insert_ledger_entry(session: AsyncSession, entry: LedgerEntry) -> None:
    """Insert within the caller's (workspace-scoped) transaction. The owner is looked up from
    the workspace, so the ledger is charged to the right identity by construction."""
    result = await session.execute(_INSERT, {"id": new_id(), **entry.model_dump()})
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise LookupError(f"workspace {entry.workspace_id} not found for ledger entry")


async def ledger_tokens_for_turn(session: AsyncSession, turn_id: uuid.UUID) -> int:
    stmt = select(
        func.coalesce(
            func.sum(
                UsageLedgerRow.input_tokens
                + UsageLedgerRow.cached_input_tokens
                + UsageLedgerRow.output_tokens
            ),
            0,
        )
    ).where(UsageLedgerRow.turn_id == turn_id)
    return int((await session.execute(stmt)).scalar_one())


# The quota counts charged (weighted) tokens, not raw ones.
_TOKENS = func.coalesce(func.sum(UsageLedgerRow.charged_tokens), 0)


class SqlLedgerReader:
    """Sums a user's ledger one workspace at a time: the ledger is under workspace RLS, so each
    sum runs in that workspace's scope and counts only rows charged to this user."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def tokens_used(self, user_id: uuid.UUID, workspace_ids: Sequence[uuid.UUID]) -> int:
        total = 0
        for workspace_id in workspace_ids:
            scope = WorkspaceScope(workspace_id=workspace_id, user_id=user_id)
            async with self._db.workspace(scope) as session:
                stmt = select(_TOKENS).where(UsageLedgerRow.owner_user_id == user_id)
                total += int((await session.execute(stmt)).scalar_one())
        return total


__all__ = [
    "SqlLedgerReader",
    "UsageLedgerRow",
    "insert_ledger_entry",
    "ledger_tokens_for_turn",
]

"""SQL usage ledger."""

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from secondmind.core import new_id
from secondmind.metering import LedgerEntry
from secondmind.metering.adapters.tables import UsageLedgerRow

_INSERT = text(
    """
    INSERT INTO usage_ledger (
        id, workspace_id, owner_user_id, turn_id, step, provider, model,
        input_tokens, cached_input_tokens, output_tokens, cost_usd, price_version
    )
    SELECT :id, w.id, w.owner_user_id, :turn_id, :step, :provider, :model,
           :input_tokens, :cached_input_tokens, :output_tokens, :cost_usd, :price_version
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


__all__ = ["UsageLedgerRow", "insert_ledger_entry", "ledger_tokens_for_turn"]

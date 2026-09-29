"""SQL usage ledger."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from secondmind.config import AppConfig
from secondmind.core import WorkspaceScope, new_id, usd
from secondmind.memory.adapters import Database
from secondmind.metering import LedgerEntry, SpendGate, SpendLimits, Spent
from secondmind.metering.adapters.inmemory import InMemorySpendStore
from secondmind.metering.adapters.redis_spend import RedisSpendStore
from secondmind.metering.adapters.tables import UsageLedgerRow

# A call in a system turn (housekeeping) is the app's cost even when the entry doesn't say so.
_INSERT = text(
    """
    INSERT INTO usage_ledger (
        id, workspace_id, owner_user_id, turn_id, step, provider, model,
        input_tokens, cached_input_tokens, output_tokens, cost_usd, charged_tokens,
        price_version, system
    )
    SELECT :id, w.id, w.owner_user_id, :turn_id, :step, :provider, :model,
           :input_tokens, :cached_input_tokens, :output_tokens, :cost_usd, :charged_tokens,
           :price_version,
           :system OR coalesce(
               (SELECT t.kind = 'system' FROM turns t
                 WHERE t.id = :turn_id AND t.workspace_id = w.id), false)
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


_COST = func.coalesce(func.sum(UsageLedgerRow.cost_usd), 0)
_TOKENS = func.coalesce(func.sum(UsageLedgerRow.charged_tokens), 0)


class SqlLedgerReader:
    """Sums a user's ledger one workspace at a time: the ledger is under workspace RLS, so each
    sum runs in that workspace's scope and counts only rows charged to this user (system usage,
    which the app pays for, is left out)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def spent(self, user_id: uuid.UUID, workspace_ids: Sequence[uuid.UUID]) -> Spent:
        dollars, tokens = Decimal(0), 0
        for workspace_id in workspace_ids:
            scope = WorkspaceScope(workspace_id=workspace_id, user_id=user_id)
            async with self._db.workspace(scope) as session:
                stmt = select(_COST, _TOKENS).where(
                    UsageLedgerRow.owner_user_id == user_id, UsageLedgerRow.system.is_(False)
                )
                cost, charged = (await session.execute(stmt)).one()
                dollars += Decimal(cost)
                tokens += int(charged)
        return Spent(usd=dollars, tokens=tokens)


class SqlSpendReader:
    """The app's total spend since a moment, per provider, across every workspace and including
    system usage: totals only, through a ``SECURITY DEFINER`` function (the app role can't read
    other workspaces' rows, and the function returns none of them)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def since(self, moment: datetime) -> dict[str, Decimal]:
        async with self._db.identity() as session:
            rows = await session.execute(
                text("SELECT provider, cost_usd FROM global_spend(:since)"), {"since": moment}
            )
            return {str(provider): Decimal(cost) for provider, cost in rows.all()}


def spend_limits(config: AppConfig) -> SpendLimits:
    """The gate's limits from the environment-only settings (ADR-0032); the providers are the
    real ones this deployment's routing calls (none when everything is on the fake provider)."""
    settings = config.settings
    routing = config.routing
    providers = sorted(
        {
            ref.provider
            for route in routing.routes.values()
            for ref in (route.primary, route.fallback)
            if ref is not None and ref.provider != "fake"
        }
    )
    return SpendLimits(
        daily_usd=usd(settings.spend_cap_daily_usd),
        monthly_usd=usd(settings.spend_cap_monthly_usd),
        warn_ratio=settings.spend_cap_warn_ratio,
        credits_usd={k: usd(v) for k, v in settings.provider_credits_usd.items()},
        credit_since=settings.provider_credit_since,
        kill_switch=settings.kill_switch,
        providers=tuple(providers),
    )


def build_gate(config: AppConfig) -> SpendGate:
    """The spend gate over the shared Redis state: one per process, on every model router."""
    store = RedisSpendStore.from_url(str(config.settings.redis_url))
    return SpendGate(store, spend_limits(config))


__all__ = [
    "InMemorySpendStore",
    "RedisSpendStore",
    "SqlLedgerReader",
    "SqlSpendReader",
    "UsageLedgerRow",
    "build_gate",
    "insert_ledger_entry",
    "ledger_tokens_for_turn",
    "spend_limits",
]

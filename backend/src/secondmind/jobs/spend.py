"""Spend counters (ADR-0032): reset from the usage ledger, which is the source of truth."""

from typing import Any

from secondmind.jobs.memory import _deps
from secondmind.observability import get_logger

log = get_logger(__name__)

# How often the counters are reconciled (the worker's cron schedule).
RECONCILE_SPEND_EVERY_MINUTES = 10


async def reconcile_spend(ctx: dict[str, Any]) -> dict[str, float]:
    """Set the day, month and per-provider spend counters to what the ledger says."""
    deps = _deps(ctx)
    if deps.gate is None or deps.spend is None:
        return {}
    totals = await deps.gate.reconcile(deps.spend)
    log.info(
        "job.reconcile_spend_done",
        day_usd=float(totals.day),
        month_usd=float(totals.month),
        providers={k: float(v) for k, v in totals.providers.items()},
    )
    return {"day_usd": float(totals.day), "month_usd": float(totals.month)} | {
        f"{k}_usd": float(v) for k, v in totals.providers.items()
    }


__all__ = ["RECONCILE_SPEND_EVERY_MINUTES", "reconcile_spend"]

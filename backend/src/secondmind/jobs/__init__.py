"""Background jobs run by the arq worker."""

from secondmind.jobs.guests import EXPIRE_GUESTS_AT_HOUR, EXPIRE_GUESTS_AT_MINUTE, expire_guests
from secondmind.jobs.heartbeat import heartbeat
from secondmind.jobs.memory import (
    DEFER_SECONDS,
    DEPS_KEY,
    EMBED_PENDING_EVERY_MINUTES,
    EXPIRE_QUICK_EVERY_MINUTES,
    JobDeferred,
    JobDeps,
    backfill_conversation,
    embed_pending,
    expire_quick,
    fetch_link,
    index_conversation,
    rerender_entity_keys,
)
from secondmind.jobs.spend import RECONCILE_SPEND_EVERY_MINUTES, reconcile_spend

JOBS = (
    heartbeat,
    fetch_link,
    rerender_entity_keys,
    expire_quick,
    index_conversation,
    backfill_conversation,
    reconcile_spend,
    embed_pending,
    expire_guests,
)

__all__ = [
    "DEFER_SECONDS",
    "DEPS_KEY",
    "EMBED_PENDING_EVERY_MINUTES",
    "EXPIRE_GUESTS_AT_HOUR",
    "EXPIRE_GUESTS_AT_MINUTE",
    "EXPIRE_QUICK_EVERY_MINUTES",
    "JOBS",
    "RECONCILE_SPEND_EVERY_MINUTES",
    "JobDeferred",
    "JobDeps",
    "backfill_conversation",
    "embed_pending",
    "expire_guests",
    "expire_quick",
    "fetch_link",
    "heartbeat",
    "index_conversation",
    "reconcile_spend",
    "rerender_entity_keys",
]

"""Background jobs run by the arq worker."""

from secondmind.jobs.heartbeat import heartbeat
from secondmind.jobs.memory import (
    DEPS_KEY,
    EXPIRE_QUICK_EVERY_MINUTES,
    JobDeps,
    backfill_conversation,
    expire_quick,
    index_conversation,
    rerender_entity_keys,
)

JOBS = (
    heartbeat,
    rerender_entity_keys,
    expire_quick,
    index_conversation,
    backfill_conversation,
)

__all__ = [
    "DEPS_KEY",
    "EXPIRE_QUICK_EVERY_MINUTES",
    "JOBS",
    "JobDeps",
    "backfill_conversation",
    "expire_quick",
    "heartbeat",
    "index_conversation",
    "rerender_entity_keys",
]

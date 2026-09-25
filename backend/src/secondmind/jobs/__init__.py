"""Background jobs run by the arq worker."""

from secondmind.jobs.heartbeat import heartbeat
from secondmind.jobs.memory import (
    DEPS_KEY,
    EXPIRE_QUICK_EVERY_MINUTES,
    JobDeps,
    expire_quick,
    rerender_entity_keys,
)

JOBS = (heartbeat, rerender_entity_keys, expire_quick)

__all__ = [
    "DEPS_KEY",
    "EXPIRE_QUICK_EVERY_MINUTES",
    "JOBS",
    "JobDeps",
    "expire_quick",
    "heartbeat",
    "rerender_entity_keys",
]

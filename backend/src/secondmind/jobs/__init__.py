"""Background jobs run by the arq worker."""

from secondmind.jobs.heartbeat import heartbeat

JOBS = (heartbeat,)

__all__ = ["JOBS", "heartbeat"]

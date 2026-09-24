"""arq adapters: the queue client (the worker entrypoint is ``worker.WorkerSettings``)."""

from secondmind.jobs.adapters.queue import QueueClient

__all__ = ["QueueClient"]

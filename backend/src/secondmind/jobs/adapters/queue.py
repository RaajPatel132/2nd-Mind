"""Queue client for enqueueing jobs and checking Redis health."""

from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from arq.jobs import Job


class QueueClient:
    def __init__(self, redis_url: str) -> None:
        self._settings = RedisSettings.from_dsn(redis_url)
        self._pool: ArqRedis | None = None

    async def _get(self) -> ArqRedis:
        if self._pool is None:
            self._pool = await create_pool(self._settings)
        return self._pool

    async def ping(self) -> bool:
        pool = await self._get()
        return bool(await pool.ping())

    async def enqueue(self, name: str, *args: Any, **kwargs: Any) -> Job:
        pool = await self._get()
        job = await pool.enqueue_job(name, *args, **kwargs)
        if job is None:
            raise RuntimeError(f"job {name!r} was not enqueued (duplicate id?)")
        return job

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None

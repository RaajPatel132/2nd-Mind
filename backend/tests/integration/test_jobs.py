"""S1.5: the worker runs arq with a heartbeat job; enqueueing it sees it complete."""

import importlib

import pytest
from arq.connections import RedisSettings, create_pool
from arq.worker import Worker

from tests.conftest import BASE_ENV

pytestmark = pytest.mark.integration


async def test_heartbeat_job_is_enqueued_and_completes(
    redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in (BASE_ENV | {"REDIS_URL": redis_url}).items():
        monkeypatch.setenv(key, value)
    worker_module = importlib.import_module("secondmind.jobs.adapters.worker")
    settings = worker_module.WorkerSettings

    from secondmind.jobs.adapters import QueueClient  # noqa: PLC0415

    queue = QueueClient(redis_url)
    assert await queue.ping()
    job = await queue.enqueue("heartbeat", note="integration")

    worker = Worker(
        functions=settings.functions,
        redis_settings=RedisSettings.from_dsn(redis_url),
        burst=True,
        poll_delay=0.05,
        handle_signals=False,
    )
    try:
        await worker.main()
        result = await job.result(timeout=10)
    finally:
        await worker.close()
        await queue.aclose()

    assert result["status"] == "ok"
    assert result["note"] == "integration"
    pool = await create_pool(RedisSettings.from_dsn(redis_url))
    info = await job.info()
    await pool.aclose()
    assert info is not None
    assert info.function == "heartbeat"

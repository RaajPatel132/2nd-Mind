"""S1.5 / S2.9 / S2.14: the worker runs arq jobs: the heartbeat, and memory housekeeping as
system turns (entity key re-rendering and quick-layer expiry)."""

import importlib
from datetime import timedelta
from types import ModuleType
from typing import Any

import pytest
from arq.connections import RedisSettings, create_pool
from arq.worker import Worker

from secondmind.agent import TurnKind, TurnStatus
from secondmind.agent.adapters import SqlTurnStore
from secondmind.auth.adapters import SqlIdentityStore
from secondmind.core import utc_now
from secondmind.jobs import expire_quick
from secondmind.memory import Memory
from secondmind.memory.adapters import Database, sql_memory
from tests.conftest import BASE_ENV
from tests.integration.conftest import PgUrls
from tests.integration.memory_seed import real_turn, seed_memory, workspace
from tests.unit.memory.helpers import create, item

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


def _worker_module(env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    # The module reads its config at import; reload so it sees this test's database.
    return importlib.reload(importlib.import_module("secondmind.jobs.adapters.worker"))


async def test_memory_jobs_run_as_system_turns_in_the_worker(
    pg_urls: PgUrls,
    redis_url: str,
    app_db: Database,
    identity: SqlIdentityStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = BASE_ENV | {
        "REDIS_URL": redis_url,
        "DATABASE_URL": pg_urls.app,
        "MODEL_PROVIDER_MODE": "fake",
    }
    worker_module = _worker_module(env, monkeypatch)
    settings = worker_module.WorkerSettings
    scope = await workspace(identity)
    memory = Memory(sql_memory(app_db))
    seeded = await seed_memory(app_db, scope)
    stale = create(
        item(
            "Buy stamps",
            in_quick=True,
            quick_reason="mentioned in the last 7 days",
            mentioned_at=utc_now() - timedelta(days=10),
            quick_until=utc_now() - timedelta(hours=1),
        )
    )
    writer = memory.writer(scope, await real_turn(app_db, scope))
    writer.add(stale)
    await writer.commit()

    from secondmind.jobs.adapters import QueueClient  # noqa: PLC0415

    queue = QueueClient(redis_url)
    job = await queue.enqueue(
        "rerender_entity_keys",
        str(scope.workspace_id),
        str(scope.user_id),
        [str(seeded.person_id)],
    )
    worker = Worker(
        functions=settings.functions,
        redis_settings=RedisSettings.from_dsn(redis_url),
        on_startup=settings.on_startup,
        on_shutdown=settings.on_shutdown,
        burst=True,
        poll_delay=0.05,
        handle_signals=False,
    )
    try:
        await worker.main()
        result = await job.result(timeout=20)
    finally:
        await worker.close()
        await queue.aclose()
    assert result["status"] == "completed", result

    ctx: dict[str, Any] = {}
    await settings.on_startup(ctx)
    try:
        summary = await expire_quick(ctx)
    finally:
        await settings.on_shutdown(ctx)
    assert summary["failed"] == 0
    stored = await memory.reader(scope).item(stale.item_id)
    assert stored is not None
    assert not stored.in_quick

    recent = await SqlTurnStore(app_db, scope).recent(limit=10)
    system = [t for t in recent if t.kind is TurnKind.SYSTEM]
    assert {t.input for t in system} == {
        "Re-render search keys after an entity change",
        "Tidy the quick layer",
    }
    assert all(t.status is TurnStatus.COMPLETED for t in system)

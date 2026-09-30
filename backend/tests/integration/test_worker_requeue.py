"""R.12 / ledger 48: the worker's shutdown, exercised for real. A job still running when the grace
period ends is cancelled and goes back on the queue, and the next worker runs it again; a job
that finishes inside the grace period is not run twice.

The arq worker is the real one, on a real Redis; the job functions are stand-ins, so this checks
the shutdown and retry behaviour our WorkerSettings rely on (`retry_jobs`, `max_tries`,
`job_completion_wait`), not any job's own logic. SIGTERM is delivered the way arq's own handler
receives it (``handle_sig_wait_for_completion``)."""

import asyncio
import contextlib
import signal
from typing import Any

import pytest
from arq.connections import RedisSettings, create_pool
from arq.worker import Worker, func

pytestmark = pytest.mark.integration


def _worker(functions: list[Any], redis_url: str, **overrides: Any) -> Worker:
    return Worker(
        functions=functions,
        redis_settings=RedisSettings.from_dsn(redis_url),
        poll_delay=0.05,
        handle_signals=False,
        retry_jobs=True,
        max_tries=3,
        job_completion_wait=1,
        **overrides,
    )


async def _sigterm(worker: Worker, task: "asyncio.Task[None]") -> None:
    worker.handle_sig_wait_for_completion(signal.SIGTERM)
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=20)
    # close() gathers the job tasks, and the one that was cancelled re-raises the cancellation.
    with contextlib.suppress(asyncio.CancelledError):
        await worker.close()


def test_the_worker_settings_the_test_relies_on_are_the_ones_the_worker_runs_with(
    base_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in base_env.items():
        monkeypatch.setenv(key, value)
    from secondmind.jobs.adapters.worker import WorkerSettings  # noqa: PLC0415 - reads the env

    assert WorkerSettings.retry_jobs is True
    assert WorkerSettings.max_tries >= 2
    assert WorkerSettings.job_completion_wait > 0


async def test_a_job_cancelled_at_the_end_of_the_grace_period_runs_again_on_the_next_worker(
    redis_url: str,
) -> None:
    started = asyncio.Event()
    tries: list[int] = []

    async def slow_job(ctx: dict[str, Any]) -> dict[str, int]:
        tries.append(ctx["job_try"])
        if ctx["job_try"] == 1:
            started.set()
            await asyncio.sleep(120)  # still running when the one-second grace period ends
        return {"job_try": ctx["job_try"]}

    pool = await create_pool(RedisSettings.from_dsn(redis_url))
    try:
        job = await pool.enqueue_job("slow_job")
        assert job is not None
        first = _worker([func(slow_job, name="slow_job")], redis_url)
        running = asyncio.create_task(first.async_run())
        await asyncio.wait_for(started.wait(), timeout=10)

        await _sigterm(first, running)  # the job outlives the grace period and is cancelled
        assert tries == [1]
        # Back on the queue, not lost and not failed. (arq's own bookkeeping for the cancelled job
        # finishes in a shielded task just after the worker closes, so give it a moment.)
        for _ in range(50):
            if await job.status() == "queued":
                break
            await asyncio.sleep(0.1)
        assert await job.status() == "queued"

        second = _worker([func(slow_job, name="slow_job")], redis_url, burst=True)
        try:
            await second.main()
        finally:
            await second.close()
        assert await job.result(timeout=10) == {"job_try": 2}
        assert tries == [1, 2]
    finally:
        await pool.aclose()


async def test_a_job_that_finishes_inside_the_grace_period_is_not_run_again(
    redis_url: str,
) -> None:
    started = asyncio.Event()
    runs = 0

    async def quick_job(ctx: dict[str, Any]) -> str:
        nonlocal runs
        runs += 1
        started.set()
        await asyncio.sleep(0.3)  # well inside the grace period
        return "done"

    pool = await create_pool(RedisSettings.from_dsn(redis_url))
    try:
        job = await pool.enqueue_job("quick_job")
        assert job is not None
        worker = _worker([func(quick_job, name="quick_job")], redis_url)
        running = asyncio.create_task(worker.async_run())
        await asyncio.wait_for(started.wait(), timeout=10)
        await _sigterm(worker, running)
        assert await job.result(timeout=10) == "done"
        assert runs == 1
        assert await job.status() == "complete"
    finally:
        await pool.aclose()

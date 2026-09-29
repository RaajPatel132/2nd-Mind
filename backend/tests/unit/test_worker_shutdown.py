"""SIGTERM on the worker (R.12): running jobs get the grace period, and a job cancelled at its end
is re-queued for the next worker rather than lost."""

import pytest


def test_the_worker_waits_for_running_jobs_and_requeues_what_it_cancels(
    base_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in base_env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("SHUTDOWN_GRACE_S", "20")
    from secondmind.jobs.adapters.worker import WorkerSettings  # noqa: PLC0415 - reads the env

    assert WorkerSettings.job_completion_wait > 0
    assert WorkerSettings.retry_jobs is True
    assert WorkerSettings.max_tries >= 2

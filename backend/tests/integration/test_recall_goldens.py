"""S3.15: the recall golden cases as ordinary tests (the fake provider replays each case's plan
and rerank scores), and, marked ``live``, the baseline table on the configured providers
(``make eval-recall-live``)."""

import sys
from collections.abc import AsyncIterator

import pytest

from secondmind.auth.adapters import SqlIdentityStore
from secondmind.evals.fixture import Seeded
from secondmind.evals.ingest import live_router
from secondmind.evals.recall import (
    RecallCase,
    case_ids,
    explain,
    failures,
    fake_router,
    load_cases,
    report,
    run_case,
    seed_main,
    unknown_keys,
)
from secondmind.memory.adapters import Database
from secondmind.providers import ModelRouter

pytestmark = pytest.mark.integration

CASES = load_cases()


def test_there_are_enough_cases_and_every_key_is_in_the_fixture() -> None:
    assert len(CASES) >= 45
    assert unknown_keys(CASES) == {}
    shapes = [s for c in CASES for s in c.shape]
    for shape in (
        "exact", "list", "latest", "history", "time_window", "order", "count", "set",
        "entity", "semantic", "why", "situational", "conversation",
    ):  # fmt: skip
        assert shapes.count(shape) >= 2, shape
    assert sum(c.must_abstain for c in CASES) >= 5
    assert sum(len(c.shape) > 1 for c in CASES) >= 3


@pytest.fixture(scope="module")
async def router() -> AsyncIterator[ModelRouter]:
    r = fake_router(CASES)
    yield r
    await r.aclose()


@pytest.fixture(scope="module")
async def shared(app_db: Database, identity: SqlIdentityStore, router: ModelRouter) -> Seeded:
    return await seed_main(app_db, identity, router)


@pytest.mark.parametrize("case", CASES, ids=case_ids(CASES))
async def test_recall_golden(
    case: RecallCase,
    app_db: Database,
    identity: SqlIdentityStore,
    router: ModelRouter,
    shared: Seeded,
) -> None:
    seeded = await seed_main(app_db, identity, router) if case.fresh else shared
    run = await run_case(case, db=app_db, seeded=seeded, router=router)
    assert failures(run) == [], f"{case.id}: {case.title}\nreply: {run.reply}\n{explain(run)}"


@pytest.mark.live
async def test_recall_goldens_live_baseline(app_db: Database, identity: SqlIdentityStore) -> None:
    """Every case on the configured real providers; prints the table (no gate, S3.15)."""
    router = live_router()
    try:
        shared = await seed_main(app_db, identity, router)
        runs = []
        for case in CASES:
            seeded = await seed_main(app_db, identity, router) if case.fresh else shared
            runs.append(await run_case(case, db=app_db, seeded=seeded, router=router))
            sys.stderr.write(f"  {case.id}: {runs[-1].latency_ms} ms\n")
    finally:
        await router.aclose()
    sys.stdout.write(f"\nrecall golden cases (live), {len(runs)} cases\n{report(runs)}\n")

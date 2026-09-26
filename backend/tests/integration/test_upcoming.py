"""S3.14: Upcoming on the recall fixture, built on the timeline tool, grouped by local day."""

import pytest

from secondmind.auth.adapters import SqlIdentityStore
from secondmind.evals.fixture import load_fixture
from secondmind.evals.recall import eval_runner, fake_router, load_cases, seed_main
from secondmind.memory.adapters import Database

pytestmark = pytest.mark.integration


async def test_upcoming_groups_plans_routines_reminders_and_tasks_by_local_day(
    app_db: Database, identity: SqlIdentityStore
) -> None:
    router = fake_router(load_cases())
    seeded = await seed_main(app_db, identity, router)
    fixture = load_fixture()
    runner = eval_runner(app_db, router, now=fixture.instant)
    found = await runner.upcoming(seeded.scope, timezone=fixture.timezone, days=30)
    key = {v: k for k, v in seeded.items.items()}
    by_day = {d.day.isoformat(): [(key[e.item_id], e.via) for e in d.entries] for d in found.days}

    assert ("dinner_tonight", "occurred") in by_day["2026-10-06"]
    assert ("dinner_tonight", "trigger") in by_day["2026-10-06"]
    assert ("gym_routine", "routine") in by_day["2026-10-07"]  # Wednesday
    assert ("saturday_workshop", "occurred") in by_day["2026-10-10"]
    assert ("renew_passport", "trigger") in by_day["2026-10-19"]
    assert ("renew_passport", "due") in by_day["2026-10-20"]
    everything = {k for entries in by_day.values() for k, _ in entries}
    assert not everything & {"dentist_new", "book_hotel", "goa_trip", "watch_dark"}
    assert {key[t.id] for t in found.undated} >= {"fix_tap", "backup_photos"}
    assert "renew_passport" not in {key[t.id] for t in found.undated}
    assert found.note() is not None
    assert "Dinner at Saffron Street" in (found.note() or "")
    await runner.aclose()

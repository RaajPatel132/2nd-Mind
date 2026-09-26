"""S3.14: Upcoming groups by the workspace's local day, not the UTC one, and the due-soon note
appears only when something is within the next 24 hours."""

from datetime import UTC, datetime

from secondmind.core import Kind, new_id
from secondmind.memory import WriterTurn
from secondmind.retrieval import Occurrence, TimelineResult, list_upcoming
from tests.unit.memory.helpers import create, item
from tests.unit.retrieval.helpers import NOW, RecordingStore, world

LATE = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)  # 23:30 on Wed 7 Oct in Kolkata
AFTER_MIDNIGHT = datetime(2026, 10, 7, 19, 0, tzinfo=UTC)  # 00:30 on Thu 8 Oct in Kolkata
SOON = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)  # 17:30 today, within 24 hours


async def upcoming_of(*times: datetime) -> tuple[dict[str, list[str]], str | None]:
    w = await world()
    plans = [create(item(f"Plan {n}", Kind.PLAN, occurred_start=at)) for n, at in enumerate(times)]
    writer = w.memory.writer(
        w.scope,
        WriterTurn(
            turn_id=new_id(), workspace_id=w.scope.workspace_id, kind="system", now=NOW.instant
        ),
        confirmed=True,
    )
    writer.add(*plans)
    await writer.commit()
    occurrences = [
        Occurrence(p.item_id, at, None, "occurred", True)
        for p, at in zip(plans, times, strict=True)
    ]
    store = RecordingStore(results={"timeline": TimelineResult(occurrences)})
    found = await list_upcoming(
        store, w.memory.reader(w.scope), now=NOW.instant, timezone=NOW.timezone, days=30
    )
    days = {d.day.isoformat(): [e.title for e in d.entries] for d in found.days}
    return days, found.note()


async def test_entries_group_by_local_day_across_midnight() -> None:
    days, _ = await upcoming_of(LATE, AFTER_MIDNIGHT)
    # Same UTC date, two local days.
    assert days == {"2026-10-07": ["Plan 0"], "2026-10-08": ["Plan 1"]}


async def test_the_due_soon_note_is_there_only_when_something_is_within_a_day() -> None:
    _, none_soon = await upcoming_of(LATE)
    assert none_soon is None
    _, soon = await upcoming_of(SOON, LATE)
    assert soon == "Coming up in the next 24 hours: Plan 0."

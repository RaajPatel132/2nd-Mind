"""Upcoming (S3.14, §7.7): what's ahead, grouped by day in the workspace timezone.

Built on the ``timeline`` tool (so Upcoming and "what's coming up this week?" can't disagree):
plans and routine occurrences, pending time reminders, tasks due and intentions with a target
date in the window. Open tasks with no date are listed separately. The due-soon note (FR-7.2)
is built from the same list in code, with no model call.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from secondmind.core import Kind, TimeClock
from secondmind.memory import ItemRecord, MemoryReader
from secondmind.retrieval.tools import Access, Filters, RecallStore, WindowFilter

# Kinds that belong on Upcoming, and states that mean it's still ahead.
UPCOMING_KINDS = (Kind.PLAN, Kind.TASK, Kind.INTENTION)
OPEN_STATES = frozenset({"scheduled", "open", "active", "wanted"})
DUE_SOON = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class UpcomingEntry:
    item_id: uuid.UUID
    title: str
    kind: Kind
    state: str
    at: datetime
    until: datetime | None
    via: str
    routine: bool
    trigger_id: uuid.UUID | None = None  # a reminder's own id: Snooze moves the reminder


@dataclass(frozen=True, slots=True)
class UpcomingDay:
    day: date
    entries: list[UpcomingEntry]


@dataclass(slots=True)
class Upcoming:
    start: datetime
    end: datetime
    timezone: str
    days: list[UpcomingDay] = field(default_factory=list)
    undated: list[ItemRecord] = field(default_factory=list)

    @property
    def due_soon(self) -> list[UpcomingEntry]:
        """Due or happening within the next 24 hours: the chat's due-soon note."""
        return [e for d in self.days for e in d.entries if e.at < self.start + DUE_SOON]

    def note(self) -> str | None:
        soon = self.due_soon
        if not soon:
            return None
        names = list(dict.fromkeys(e.title for e in soon))
        shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
        return f"Coming up in the next 24 hours: {shown}."


async def list_upcoming(
    store: RecallStore,
    reader: MemoryReader,
    *,
    now: datetime,
    timezone: str,
    days: int,
) -> Upcoming:
    end = now + timedelta(days=days)
    result = await store.timeline(
        WindowFilter(clock=TimeClock.OCCURRED, start=now, end=end),
        Filters(kinds=UPCOMING_KINDS),
        Access(sensitive=True),  # the person's own list: nothing is hidden from them
        now=now,
        timezone=timezone,
        limit=500,
    )
    items = {i.id: i for i in await reader.items(sorted({o.item_id for o in result.occurrences}))}
    zone = ZoneInfo(timezone)
    by_day: dict[date, list[UpcomingEntry]] = {}
    seen: set[tuple[uuid.UUID, datetime]] = set()
    for o in result.occurrences:
        item = items.get(o.item_id)
        if item is None or item.state not in OPEN_STATES or (o.item_id, o.start) in seen:
            continue
        if o.start < now and (o.end is None or o.end <= now):
            continue
        seen.add((o.item_id, o.start))
        entry = UpcomingEntry(
            item_id=item.id,
            title=item.title,
            kind=item.kind,
            state=item.state,
            at=o.start,
            until=o.end,
            via=o.via,
            routine=o.via == "routine",
            trigger_id=o.trigger_id,
        )
        by_day.setdefault(o.start.astimezone(zone).date(), []).append(entry)
    out = Upcoming(start=now, end=end, timezone=timezone)
    out.days = [UpcomingDay(day=d, entries=by_day[d]) for d in sorted(by_day)]
    tasks = await store.lookup(
        Filters(kinds=(Kind.TASK,), states=("open",)), Access(sensitive=True), limit=200
    )
    open_tasks = await reader.items([h.item_id for h in tasks.hits])
    out.undated = [
        t for t in open_tasks if t.due_at is None and t.occurred_start is None and t.id not in items
    ]
    return out

"""R.7: routines in local time. A Mon/Wed/Fri 7 am routine enters and leaves the quick layer at
7 am *local*, in a timezone with no DST and across a DST change; and there is one RRULE
expansion in the codebase."""

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from secondmind.config import DEFAULT_RESOURCES_DIR
from secondmind.core import Kind, Modality, Sensitivity, TimePrecision
from secondmind.memory import ItemContent, next_occurrence, quick_layer

RULE = "FREQ=WEEKLY;BYDAY=MO,WE,FR;BYHOUR=7;BYMINUTE=0"


def _routine(first_local: datetime) -> ItemContent:
    return ItemContent(
        kind=Kind.PLAN,
        subtype="routine",
        state="scheduled",
        title="Gym",
        text="Gym on Mon, Wed and Fri at 7 am",
        mentioned_at=first_local.astimezone(UTC) - timedelta(days=30),
        occurred_start=first_local.astimezone(UTC),
        time_precision=TimePrecision.DATETIME,
        rrule=RULE,
        modality=Modality.ASSERTED,
        sensitivity=Sensitivity.NORMAL,
        category_id=uuid.uuid4(),
    )


def _local(tz: str, *args: int) -> datetime:
    return datetime(*args, tzinfo=ZoneInfo(tz))  # type: ignore[misc]


def _quick(item: ItemContent, now: datetime, tz: str) -> tuple[bool, datetime | None]:
    decision = quick_layer(item, now=now, timezone=tz, horizon_days=1, recent_days=0)
    return decision.in_quick, decision.until


def test_a_kolkata_routine_enters_and_leaves_quick_at_7_am_there() -> None:
    tz = "Asia/Kolkata"
    gym = _routine(_local(tz, 2026, 10, 5, 7, 0))  # Monday 5 October, 7 am IST
    # Wednesday 7 am IST is 25 hours after Tuesday 6 am: not yet within a day.
    assert _quick(gym, _local(tz, 2026, 10, 6, 6, 0), tz) == (False, None)
    # At Tuesday 8 am it's 23 hours away: in, until the Wednesday session ends (8 am IST).
    assert _quick(gym, _local(tz, 2026, 10, 6, 8, 0), tz) == (True, _local(tz, 2026, 10, 7, 8, 0))
    # During the session it stays; once it's over, Friday is two days off: out.
    assert _quick(gym, _local(tz, 2026, 10, 7, 7, 59), tz)[0]
    assert _quick(gym, _local(tz, 2026, 10, 7, 8, 1), tz) == (False, None)


def test_a_new_york_routine_stays_at_7_am_local_across_the_dst_change() -> None:
    tz = "America/New_York"
    gym = _routine(_local(tz, 2026, 10, 26, 7, 0))  # Monday 26 October, 7 am EDT (11:00 UTC)
    before = next_occurrence(gym, _local(tz, 2026, 10, 27, 9, 0), tz)
    assert before is not None
    assert before[0] == _local(tz, 2026, 10, 28, 7, 0)  # Wednesday, 7 am EDT = 11:00 UTC
    # Clocks go back on Sunday 1 November: Monday 2 November 7 am is EST, 12:00 UTC.
    after = next_occurrence(gym, _local(tz, 2026, 11, 1, 12, 0), tz)
    assert after is not None
    assert after[0] == _local(tz, 2026, 11, 2, 7, 0)
    assert after[0].astimezone(UTC).hour == 12
    in_quick, until = _quick(gym, _local(tz, 2026, 11, 1, 8, 0), tz)
    assert in_quick
    assert until == _local(tz, 2026, 11, 2, 8, 0)


def test_nothing_outside_the_recurrence_module_parses_an_rrule() -> None:
    src = DEFAULT_RESOURCES_DIR / "src" / "secondmind"
    home = src / "memory" / "recurrence.py"
    offenders: list[str] = []
    for path in sorted(src.rglob("*.py")):
        if path == home:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "dateutil.rrule":
                offenders.append(f"{path.relative_to(src)}: from dateutil.rrule import …")
            elif isinstance(node, ast.Import) and any(
                a.name == "dateutil.rrule" for a in node.names
            ):
                offenders.append(f"{path.relative_to(src)}: import dateutil.rrule")
            elif isinstance(node, ast.Name | ast.Attribute) and "rrulestr" in ast.unparse(node):
                offenders.append(f"{path.relative_to(src)}: rrulestr")
    assert offenders == [], "RRULEs are parsed only in memory/recurrence.py"
    assert Path(home).exists()

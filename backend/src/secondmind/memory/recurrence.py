"""Recurring times (RFC 5545 RRULEs): the one place a rule is parsed and expanded (R.7).

A routine is stored as its first occurrence (a UTC instant) and a rule. Rules are always
expanded in the workspace's **local** time: ``BYHOUR=7`` is 7 am there, before and after a DST
change, so every expansion goes through :func:`expand_rrule`. Nothing else in the codebase
calls ``rrulestr`` (a test enforces it).
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil.rrule import rrule as RRule  # noqa: N812 - dateutil's class name
from dateutil.rrule import rrulestr


def parse_rrule(rule: str, local_start: datetime) -> RRule:
    """``rule`` anchored at ``local_start``, a naive local wall-clock time. Raises
    ``ValueError`` for anything that isn't a valid RRULE."""
    parsed = rrulestr(rule.removeprefix("RRULE:"), dtstart=local_start)
    if not isinstance(parsed, RRule):
        raise ValueError(f"expected one RRULE, got {rule!r}")
    return parsed


def validate_rrule(rule: str, start: datetime) -> str:
    """Raise ``ValueError`` unless ``rule`` is a valid RRULE; the rule without its prefix."""
    parse_rrule(rule, start.replace(tzinfo=None))
    return rule.removeprefix("RRULE:")


def first_local_occurrence(rule: str, local_start: datetime, after: datetime) -> datetime | None:
    """The first occurrence at or after ``after`` of ``rule`` anchored at ``local_start``; both
    are naive local wall-clock times, and so is the result."""
    return parse_rrule(rule, local_start).after(after, inc=True)


def expand_rrule(
    rrule: str,
    first: datetime,
    length: timedelta,
    *,
    start: datetime | None,
    end: datetime | None,
    timezone: str,
    limit: int = 100,
) -> list[tuple[datetime, datetime]]:
    """Occurrences of an RFC 5545 rule overlapping ``[start, end)``, as UTC ``(start, end)``
    pairs. The rule is expanded in the workspace's local time (``BYHOUR=7`` is 7 am there,
    across DST changes) from ``first``, the first occurrence. An unbounded window is capped at
    ``limit``."""
    tz = ZoneInfo(timezone)
    local_first = first.astimezone(tz).replace(tzinfo=None)
    rule = parse_rrule(rrule, local_first)
    lo = (start - length) if start is not None else first
    cursor = rule.after(lo.astimezone(tz).replace(tzinfo=None), inc=True)
    out: list[tuple[datetime, datetime]] = []
    while cursor is not None and len(out) < limit:
        begins = cursor.replace(tzinfo=tz).astimezone(UTC)
        if end is not None and begins >= end:
            break
        if start is None or begins + length > start:
            out.append((begins, begins + length))
        cursor = rule.after(cursor, inc=False)
    return out


def next_occurrence_of(
    rrule: str, first: datetime, length: timedelta, *, now: datetime, timezone: str
) -> tuple[datetime, datetime] | None:
    """The occurrence running at ``now``, or the next one after it, as UTC ``(start, end)``."""
    upcoming = expand_rrule(rrule, first, length, start=now, end=None, timezone=timezone, limit=1)
    return upcoming[0] if upcoming else None

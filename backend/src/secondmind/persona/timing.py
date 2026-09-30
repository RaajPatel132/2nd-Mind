"""Times in a seed or a fixture: local wall-clock text, or days from the anchor.

A seed written with days from its anchor (``-12d 07:30``, ``+3d``) carries no calendar date, so
moving the anchor moves everything with it (S4.10, S4.11). The recall fixture keeps its literal
dates; both resolve through :func:`resolve_time`.
"""

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

_RELATIVE = re.compile(r"^([+-]?\d+)d(?:\s+(\d{1,2}):(\d{2}))?$")


def local_instant(text: str, tz: str) -> datetime:
    """ "2026-09-02T06:30" (local) or "2026-09-02" or "...Z" (UTC) -> a UTC instant."""
    if text.endswith("Z"):
        return datetime.fromisoformat(text.removesuffix("Z")).replace(tzinfo=UTC)
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(tz)).astimezone(UTC)


def is_relative(text: str) -> bool:
    return _RELATIVE.match(text.strip()) is not None


def resolve_time(text: str, tz: str, now: datetime) -> datetime:
    """``text`` as a UTC instant: days from ``now``'s local date at a time of day (``-12d 07:30``,
    ``+3d``; the time defaults to now's), or a local/UTC wall-clock time (``local_instant``)."""
    match = _RELATIVE.match(text.strip())
    if match is None:
        return local_instant(text, tz)
    zone = ZoneInfo(tz)
    local_now = now.astimezone(zone)
    day = local_now.date() + timedelta(days=int(match.group(1)))
    hour, minute = (
        (int(match.group(2)), int(match.group(3)))
        if match.group(2) is not None
        else (local_now.hour, local_now.minute)
    )
    return datetime.combine(day, time(hour, minute), tzinfo=zone).astimezone(UTC)


def local_date(instant: datetime, tz: str) -> date:
    return instant.astimezone(ZoneInfo(tz)).date()


def days_to_move(anchor: datetime, today: datetime, tz: str) -> int:
    """The whole days from the seed's anchor to ``today``, by the calendar in ``tz``."""
    return (local_date(today, tz) - local_date(anchor, tz)).days

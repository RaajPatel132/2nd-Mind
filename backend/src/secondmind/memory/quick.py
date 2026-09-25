"""The quick layer by plain rules in code (S2.9): no model. The rule that fired is the rationale.

* plans within ``horizon_days`` stay until they pass;
* items with a pending time trigger stay until it fires;
* open tasks stay until they're done;
* anything mentioned in the last ``recent_days`` stays for that long;
* anything cited in enough recall turns lately ("frequently retrieved", FR-6.7) stays while it
  keeps being cited.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr

from secondmind.core import Kind, TimePrecision, TriggerOn, TriggerState
from secondmind.memory.records import ItemContent, TriggerContent

FREQUENT_REASON = "frequently retrieved"


@dataclass(frozen=True, slots=True)
class QuickDecision:
    in_quick: bool
    reason: str | None = None
    until: datetime | None = None


def next_occurrence(item: ItemContent, now: datetime) -> tuple[datetime, datetime] | None:
    """Start and end of the plan's next occurrence at or after ``now`` (RRULE-aware)."""
    if item.occurred_start is None:
        return None
    start = item.occurred_start
    length = _length(item)
    if item.rrule:
        rule = rrulestr(item.rrule, dtstart=start)
        upcoming = rule.after(now - length, inc=True)
        if upcoming is None:
            return None
        start = upcoming
    end = item.occurred_end if not item.rrule and item.occurred_end else start + length
    return start, end


def occurrences(
    item: ItemContent,
    start: datetime | None,
    end: datetime | None,
    timezone: str,
    *,
    limit: int = 100,
) -> list[tuple[datetime, datetime]]:
    """The routine's occurrences overlapping ``[start, end)``, as UTC ``(start, end)`` pairs."""
    if not item.rrule or item.occurred_start is None:
        return []
    return expand_rrule(
        item.rrule,
        item.occurred_start,
        _length(item),
        start=start,
        end=end,
        timezone=timezone,
        limit=limit,
    )


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
    """Occurrences of an RFC 5545 rule overlapping ``[start, end)``. The rule is expanded in the
    workspace's local time (``BYHOUR=7`` is 7 am there, across DST changes) from ``first``, the
    first occurrence. An unbounded window is capped at ``limit``."""
    tz = ZoneInfo(timezone)
    local_first = first.astimezone(tz).replace(tzinfo=None)
    rule = rrulestr(rrule, dtstart=local_first)
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


def occurrence_length(precision: TimePrecision | None) -> timedelta:
    match precision:
        case TimePrecision.DATETIME:
            return timedelta(hours=1)
        case TimePrecision.MONTH:
            return timedelta(days=31)
        case TimePrecision.YEAR:
            return timedelta(days=366)
        case _:
            return timedelta(days=1)


def _length(item: ItemContent) -> timedelta:
    if item.occurred_end and item.occurred_start and not item.rrule:
        return item.occurred_end - item.occurred_start
    return occurrence_length(item.time_precision)


def quick_layer(
    item: ItemContent,
    *,
    now: datetime,
    triggers: Sequence[TriggerContent] = (),
    horizon_days: int,
    recent_days: int,
    frequent: bool = False,
) -> QuickDecision:
    reasons: list[str] = []
    untils: list[datetime | None] = []
    if item.kind is Kind.PLAN and item.state == "scheduled":
        occurrence = next_occurrence(item, now)
        if (
            occurrence
            and occurrence[0] <= now + timedelta(days=horizon_days)
            and occurrence[1] > now
        ):
            reasons.append(f"plan within {horizon_days} days")
            untils.append(occurrence[1])
    pending = [
        t
        for t in triggers
        if t.on is TriggerOn.TIME
        and t.state is TriggerState.PENDING
        and t.fires_at is not None
        and t.fires_at >= now
    ]
    if pending:
        reasons.append("pending reminder")
        untils.append(max(t.fires_at for t in pending if t.fires_at is not None))
    if item.kind is Kind.TASK and item.state == "open":
        reasons.append("open task, until done")
        untils.append(None)
    recent_until = item.mentioned_at + timedelta(days=recent_days)
    if recent_days > 0 and recent_until > now:
        reasons.append(f"mentioned in the last {recent_days} days")
        untils.append(recent_until)
    if frequent:
        reasons.append(FREQUENT_REASON)
        untils.append(now + timedelta(days=max(recent_days, 1)))
    if not reasons:
        return QuickDecision(in_quick=False)
    until = None if any(u is None for u in untils) else max(u for u in untils if u is not None)
    return QuickDecision(in_quick=True, reason=reasons[0], until=until)

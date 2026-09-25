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
from datetime import datetime, timedelta

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


def _length(item: ItemContent) -> timedelta:
    if item.occurred_end and item.occurred_start and not item.rrule:
        return item.occurred_end - item.occurred_start
    match item.time_precision:
        case TimePrecision.DATETIME:
            return timedelta(hours=1)
        case TimePrecision.MONTH:
            return timedelta(days=31)
        case TimePrecision.YEAR:
            return timedelta(days=366)
        case _:
            return timedelta(days=1)


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

"""The soft channel's query text (S3.6), built in code from the question and the resolved plan,
in the words the verbalised keys use (``memory/render.py``): "last month" -> "September 2026",
"my sister" -> "Nisha (sister)", runs -> "Logged run". Keys and query then share the exact
words the lexical half matches on, even when a filter would have missed."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from secondmind.core import Kind, TimePrecision
from secondmind.ingestion import Window
from secondmind.memory import format_day
from secondmind.retrieval.plan import SubQuery
from secondmind.retrieval.tools import Filters

_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
)  # fmt: skip
_INTENT = {
    "watch": "Wish list, to watch",
    "read": "Wish list, to read",
    "learn": "Wish list, to learn",
    "try": "Wish list, to try",
    "buy": "Wish list, to buy",
    "visit": "Wish list, to visit",
    "gift": "Wish list, to gift",
}
_KIND = {
    Kind.FACT: "Fact",
    Kind.PREFERENCE: "Preference",
    Kind.PLAN: "Upcoming",
    Kind.TASK: "To do",
    Kind.INTENTION: "Wish list",
    Kind.RESOURCE: "Saved",
    Kind.NOTE: "Note",
    Kind.RULE: "Rule",
    Kind.PATTERN: "Pattern",
}


def soft_query_text(sub: SubQuery, timezone: str) -> str:
    """The question, then the plan in key words: kinds, windows, entities, attributes."""
    parts: list[str] = [sub.question]
    parts += kind_words(sub.filters)
    for window in sub.windows:
        parts.append(window_words(window, timezone))
    for entity in sub.entities:
        if entity.outcome == "matched":
            parts.extend(entity.names)
    if sub.about:
        parts.append(sub.about)
    return " ".join(" ".join(p for p in parts if p).split())


def kind_words(filters: Filters) -> list[str]:
    activity = filters.attribute[1] if filters.attribute else None
    words: list[str] = []
    for kind in filters.kinds:
        if kind is Kind.EPISODE:
            noun = activity or next(
                (s for s in filters.subtypes if s not in ("measurement", "experience")), None
            )
            words.append(f"Logged {noun}" if noun else "Logged")
        elif kind is Kind.INTENTION and filters.subtypes:
            words.extend(_INTENT.get(s, f"Wish list, to {s}") for s in filters.subtypes)
        else:
            words.append(_KIND[kind])
    if activity and not any(w.endswith(activity) for w in words):
        words.append(activity)
    return words


def window_words(window: Window, timezone: str) -> str:
    """A window in the renderer's absolute calendar words."""
    tz = ZoneInfo(timezone)
    start, end = window.start, window.end
    if start is None and end is None:
        return ""
    if window.precision is TimePrecision.YEAR and start is not None:
        return str(start.astimezone(tz).year)
    if start is None or end is None:
        local = (start or end or datetime.now(tz)).astimezone(tz)
        return f"{_MONTHS[local.month - 1]} {local.year}"
    first = start.astimezone(tz)
    last = (end - timedelta(microseconds=1)).astimezone(tz)
    span = (last.date() - first.date()).days
    if span < 7:
        days = [first + timedelta(days=n) for n in range(span + 1)]
        weekend = ["Weekend"] if all(d.weekday() >= 5 for d in days) else []
        named = [format_day(d, timezone) for d in days]
        return " ".join([*named, *weekend, *_months(first, last)])
    return " ".join(_months(first, last))


def _months(first: datetime, last: datetime) -> list[str]:
    out: list[str] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month) and len(out) < 4:
        out.append(f"{_MONTHS[month - 1]} {year}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def texts(subs: Sequence[SubQuery], timezone: str) -> list[str]:
    return [soft_query_text(s, timezone) for s in subs]

"""Deterministic time resolution (S2.5, FR-3.2, ADR-0022). The model never does date arithmetic:
extraction returns expressions verbatim with the clock they belong to, and ``resolve`` turns
each into a value, an honest precision, an RRULE for routines, and the name of the rule that
fired. ``now`` (a UTC instant plus the workspace timezone) is always passed in.

Calendar arithmetic and RRULE validation use python-dateutil; the grammar for what people say
("next Friday", "the 3rd of next month", "every other Sunday") is ours, because general parsers
get exactly these cases wrong (ADR-0022). Ambiguous readings take the most likely one, mark it
``assumed`` and name the alternative (FR-1.4).
"""

import math
import re
from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from dateutil.rrule import rrulestr

from secondmind.core import TimeClock, TimePrecision

Direction = Literal["future", "past", "auto"]

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WD_ABBR = {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3, "fri": 4,
            "sat": 5, "sun": 6}  # fmt: skip
_WD_RRULE = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december")  # fmt: skip
_MONTH_ABBR = {m[:3]: i + 1 for i, m in enumerate(MONTHS)} | {"sept": 9}
_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
            "fifteen": 15, "twenty": 20, "thirty": 30, "couple of": 2, "a couple of": 2,
            "few": 3, "a few": 3}  # fmt: skip
_ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": -1}
# Where a part of the day starts and ends (local hours).
PARTS = {"morning": (8, 12), "afternoon": (14, 17), "evening": (18, 21), "night": (21, 24)}


# Leading words that bound a validity or due clock: "since March", "until May", "by Friday".
_BOUNDS: tuple[tuple[str, Literal["since", "until", "by"]], ...] = (
    ("since", "since"),
    ("from", "since"),
    ("until", "until"),
    ("till", "until"),
    ("til", "until"),
    ("up to", "until"),
    ("by", "by"),
    ("before", "by"),
)


class UnresolvableTimeError(ValueError):
    """No rule understood the expression."""


@dataclass(frozen=True, slots=True)
class TurnNow:
    """ "Now" for a turn: one UTC instant plus the workspace timezone, captured at turn start."""

    instant: datetime
    timezone: str

    @property
    def local(self) -> datetime:
        return self.instant.astimezone(ZoneInfo(self.timezone))

    @property
    def today(self) -> date:
        return self.local.date()


@dataclass(frozen=True, slots=True)
class Resolved:
    expression: str
    clock: TimeClock
    start: datetime
    end: datetime | None
    precision: TimePrecision
    rule: str
    rrule: str | None = None
    assumed: bool = False
    alternative: str | None = None
    part_of_day: str | None = None
    bound: Literal["since", "until", "by", None] = None


# ------------------------------------------------------------------ helpers


def _norm(expression: str) -> str:
    text = expression.lower().strip()
    text = re.sub("[\u201c\u201d\"'`\u2019]", "", text)
    text = re.sub(r"[,;!?]", " ", text)
    text = re.sub(r"\.(?!\d)", " ", text)
    text = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text)
    text = re.sub(r"\bmid-?(\w)", r"mid \1", text)
    for word, n in sorted(_NUMBERS.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(
            rf"\b{word}\s+(?=(min|minute|hour|hr|day|week|fortnight|month|year)s?\b)", f"{n} ", text
        )
    return " ".join(text.split())


def _at(d: date, t: time, tz: str) -> datetime:
    return datetime.combine(d, t, tzinfo=ZoneInfo(tz)).astimezone(UTC)


def _day_window(d: date, tz: str, days: int = 1) -> tuple[datetime, datetime]:
    return _at(d, time(0), tz), _at(d + timedelta(days=days), time(0), tz)


def _month_start(year: int, month: int) -> date:
    return date(year, month, 1)


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _month_no(word: str) -> int | None:
    word = word.lower()
    if word in MONTHS:
        return MONTHS.index(word) + 1
    return _MONTH_ABBR.get(word[:4]) or _MONTH_ABBR.get(word[:3]) if len(word) >= 3 else None


def _weekday_no(word: str) -> int | None:
    word = word.lower().rstrip("s") if word.lower() not in ("tues", "thurs") else word.lower()
    if word in WEEKDAYS:
        return WEEKDAYS.index(word)
    return _WD_ABBR.get(word)


_MONTH_RE = (
    r"(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_WD_RE = (
    r"\b(?P<wd>mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|"
    r"sat(?:urday)?|sun(?:day)?)s?\b"
)


@dataclass(frozen=True, slots=True)
class _ClockTime:
    value: time
    assumed: bool = False
    alternative: time | None = None


def _clock_time(text: str) -> tuple[str, _ClockTime | None, str | None]:  # noqa: PLR0911
    """Pull a time of day ("at 7", "7:30 pm", "19:00", "noon") or a part of day out of text."""
    part: str | None = None
    if re.search(r"\btonight\b", text):
        part, text = "night", re.sub(r"\btonight\b", "today", text)
    elif re.search(r"\blast night\b", text):
        part, text = "night", re.sub(r"\blast night\b", "yesterday", text)
    else:
        match = re.search(r"\b(?:(this|in the)\s+)?(morning|afternoon|evening|night)\b", text)
        if match:
            part = match.group(2)
            filler = " today " if match.group(1) == "this" else " "
            text = text[: match.start()] + filler + text[match.end() :]
    text = " ".join(text.split())
    patterns = (
        (r"\b(?:at\s+)?(\d{1,2}):(\d{2})\s*(am|pm)\b", "hm_ampm"),
        (r"\b(?:at\s+)?(\d{1,2})\s*(am|pm)\b", "h_ampm"),
        (r"\b(?:at\s+)?([01]?\d|2[0-3]):([0-5]\d)\b", "hm24"),
        (r"\bat\s+(\d{1,2})\b(?!\s*(?:st|nd|rd|th|/|-|\d))", "h_bare"),
    )
    for regex, kind in patterns:
        match = re.search(regex, text)
        if not match:
            continue
        text = (text[: match.start()] + " " + text[match.end() :]).strip()
        if kind == "hm_ampm":
            hour, minute, ampm = int(match.group(1)), int(match.group(2)), match.group(3)
            return " ".join(text.split()), _ClockTime(time(_h24(hour, ampm), minute)), part
        if kind == "h_ampm":
            hour, ampm = int(match.group(1)), match.group(2)
            return " ".join(text.split()), _ClockTime(time(_h24(hour, ampm))), part
        if kind == "hm24":
            hour, minute = int(match.group(1)), int(match.group(2))
            return " ".join(text.split()), _ClockTime(time(hour, minute)), part
        hour = int(match.group(1))
        if hour > 12 or hour == 0:
            return " ".join(text.split()), _ClockTime(time(hour % 24)), part
        if part in ("evening", "night", "afternoon") and hour < 12:
            return " ".join(text.split()), _ClockTime(time(hour + 12)), part
        if part == "morning":
            return " ".join(text.split()), _ClockTime(time(hour)), part
        if hour == 12:
            return " ".join(text.split()), _ClockTime(time(12)), part
        am, pm = time(hour), time(hour + 12)
        # 1-6 is almost always afternoon or evening; 7-11 usually morning. Say which we picked.
        chosen, other = (pm, am) if hour <= 6 else (am, pm)
        return " ".join(text.split()), _ClockTime(chosen, True, other), part
    for word, value in (("noon", time(12)), ("midday", time(12)), ("midnight", time(0))):
        if re.search(rf"\b(?:at\s+)?{word}\b", text):
            text = re.sub(rf"\b(?:at\s+)?{word}\b", " ", text)
            return " ".join(text.split()), _ClockTime(value), part
    return " ".join(text.split()), None, part


def _h24(hour: int, ampm: str) -> int:
    hour %= 12
    return hour + 12 if ampm == "pm" else hour


# ------------------------------------------------------------------ resolution


@dataclass(frozen=True, slots=True)
class _Day:
    """A resolved calendar span before a time of day is applied."""

    start: date
    days: int
    precision: TimePrecision
    rule: str
    assumed: bool = False
    alternative: str | None = None


def resolve(
    expression: str,
    clock: TimeClock,
    now: TurnNow,
    *,
    direction: Direction = "auto",
    recurring: bool = False,
) -> Resolved:
    """Resolve one verbatim expression on one clock against ``now``."""
    text = _norm(expression)
    bound: Literal["since", "until", "by", None] = None
    for word, kind in _BOUNDS:
        if text.startswith(word + " "):
            text = text[len(word) + 1 :]
            bound = kind
            break
    if direction == "auto":
        direction = _default_direction(clock, bound)

    routine = _routine(text, now)
    if routine is not None:
        return replace(routine, expression=expression, clock=clock, bound=bound)

    text, clock_time, part = _clock_time(text)
    exact = _exact_instant(text, now)
    if exact is not None:
        return replace(exact, expression=expression, clock=clock, bound=bound)

    day = _span(text, now, direction)
    if day is None:
        bare = re.sub(r"^(on|at|in the|in|the|around|about|for)\s+", "", text)
        if re.search(r"\d", bare):
            bare = " ".join(re.sub(r"\b(the|of)\b", " ", bare).split())
        day = _span(bare, now, direction) or _weekday_and_date(bare, now, direction)
    leftover = re.sub(r"\b(on|at|in|the|of|around|about)\b", "", text).strip()
    if day is None:
        if leftover:
            raise UnresolvableTimeError(f"no rule understood {expression!r}")
        if clock_time is not None or part is not None:
            day = _Day(now.today, 1, TimePrecision.DAY, "today_implied")
            passed = (
                clock_time is not None
                and _at(now.today, clock_time.value, now.timezone) < now.instant
            )
            if passed and direction == "future":
                day = _Day(now.today + timedelta(days=1), 1, TimePrecision.DAY, "next_time_of_day")
        else:
            raise UnresolvableTimeError(f"no rule understood {expression!r}")

    result = _apply_time(day, clock_time, part, now)
    if recurring:
        result = _yearly(result, now)
    return replace(result, expression=expression, clock=clock, bound=bound)


def _weekday_and_date(text: str, now: TurnNow, direction: Direction) -> _Day | None:
    """ "Thursday 2 October": the date decides; a weekday that disagrees is flagged."""
    match = re.match(rf"^{_WD_RE}\s+(?P<rest>.+)$", text)
    if not match:
        return None
    day = _span(match.group("rest"), now, direction)
    wd = _weekday_no(match.group("wd"))
    if day is None or wd is None or day.precision is not TimePrecision.DAY:
        return day
    if day.start.weekday() == wd:
        return replace(day, rule=day.rule + "+weekday")
    offset = (wd - day.start.weekday() + 3) % 7 - 3
    other = day.start + timedelta(days=offset)
    return replace(
        day,
        rule=day.rule + "+weekday_mismatch",
        assumed=True,
        alternative=f"{WEEKDAYS[wd].capitalize()} {other.day} {MONTHS[other.month - 1].title()}",
    )


def _default_direction(clock: TimeClock, bound: str | None) -> Direction:
    if clock is TimeClock.OCCURRED:
        return "future"
    if clock is TimeClock.VALID:
        return "past" if bound == "since" else "future"
    return "future"


def _apply_time(day: _Day, ct: _ClockTime | None, part: str | None, now: TurnNow) -> Resolved:
    tz = now.timezone
    if ct is not None and day.precision is TimePrecision.DAY and day.days == 1:
        start = _at(day.start, ct.value, tz)
        alternative = day.alternative
        if ct.assumed and ct.alternative is not None:
            alternative = (
                f"{ct.alternative.strftime('%H:%M')} instead of {ct.value.strftime('%H:%M')}"
            )
        return Resolved(
            expression="",
            clock=TimeClock.OCCURRED,
            start=start,
            end=None,
            precision=TimePrecision.DATETIME,
            rule=day.rule + "+time",
            assumed=day.assumed or ct.assumed,
            alternative=alternative,
            part_of_day=part,
        )
    if part is not None and day.precision is TimePrecision.DAY and day.days == 1:
        first, last = PARTS[part]
        return Resolved(
            expression="",
            clock=TimeClock.OCCURRED,
            start=_at(day.start, time(first), tz),
            end=_at(day.start, time(0), tz) + timedelta(hours=last),
            precision=TimePrecision.DATETIME,
            rule=day.rule + "+part_of_day",
            assumed=day.assumed,
            alternative=day.alternative,
            part_of_day=part,
        )
    start, end = _day_window(day.start, tz, day.days)
    if day.precision is TimePrecision.MONTH:
        nxt = day.start + relativedelta(months=1)
        end = _at(nxt, time(0), tz)
    elif day.precision is TimePrecision.YEAR:
        end = _at(date(day.start.year + 1, 1, 1), time(0), tz)
    return Resolved(
        expression="",
        clock=TimeClock.OCCURRED,
        start=start,
        end=end if day.days != 1 or day.precision is not TimePrecision.DAY else None,
        precision=day.precision,
        rule=day.rule,
        assumed=day.assumed,
        alternative=day.alternative,
        part_of_day=part,
    )


def _exact_instant(text: str, now: TurnNow) -> Resolved | None:
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:t|\s)(\d{2}):(\d{2})(?::\d{2})?", text)
    if iso:
        y, m, d, hh, mm = map(int, iso.groups())
        return Resolved(
            "", TimeClock.OCCURRED, _at(date(y, m, d), time(hh, mm), now.timezone), None,
            TimePrecision.DATETIME, "iso_datetime",
        )  # fmt: skip
    rel = re.fullmatch(r"(?:in\s+)?(\d+)\s*(min|minute|hour|hr)s?(?:\s+from now)?", text)
    if rel:
        n, unit = int(rel.group(1)), rel.group(2)
        delta = timedelta(minutes=n) if unit.startswith("min") else timedelta(hours=n)
        return Resolved(
            "", TimeClock.OCCURRED, now.instant + delta, None, TimePrecision.DATETIME, "in_n_units"
        )
    ago = re.fullmatch(r"(\d+)\s*(min|minute|hour|hr)s?\s+ago", text)
    if ago:
        n, unit = int(ago.group(1)), ago.group(2)
        delta = timedelta(minutes=n) if unit.startswith("min") else timedelta(hours=n)
        return Resolved(
            "", TimeClock.OCCURRED, now.instant - delta, None, TimePrecision.DATETIME, "n_units_ago"
        )
    return None


def _span(text: str, now: TurnNow, direction: Direction) -> _Day | None:
    for rule in _SPAN_RULES:
        found = rule(text, now, direction)
        if found is not None:
            return found
    return None


def _relative_day(text: str, now: TurnNow, direction: Direction) -> _Day | None:
    today = now.today
    table = {
        "today": (0, "today"),
        "now": (0, "today"),
        "tomorrow": (1, "tomorrow"),
        "yesterday": (-1, "yesterday"),
        "day after tomorrow": (2, "day_after_tomorrow"),
        "day before yesterday": (-2, "day_before_yesterday"),
        "last night": (-1, "last_night"),
    }
    if text in table:
        offset, rule = table[text]
        return _Day(today + timedelta(days=offset), 1, TimePrecision.DAY, rule)
    return None


def _iso_or_numeric(text: str, now: TurnNow, direction: Direction) -> _Day | None:
    iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if iso:
        iso_y, iso_m, iso_d = map(int, iso.groups())
        return _Day(date(iso_y, iso_m, iso_d), 1, TimePrecision.DAY, "iso_date")
    num = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?", text)
    if not num:
        return None
    a, b = int(num.group(1)), int(num.group(2))
    month_first = now.timezone.startswith("America/")
    day_no, month_no = (b, a) if month_first else (a, b)
    ambiguous = a <= 12 and b <= 12 and a != b
    if month_no > 12:
        day_no, month_no = month_no, day_no
        ambiguous = False
    year = num.group(3)
    if year is None:
        d = _next_date(month_no, day_no, now, direction)
    else:
        y = int(year) + (2000 if len(year) == 2 else 0)
        d = date(y, month_no, day_no)
    alternative = None
    if ambiguous:
        alternative = f"{b}/{a} read the other way round"
    rule = "numeric_date_mdy" if month_first else "numeric_date_dmy"
    return _Day(d, 1, TimePrecision.DAY, rule, assumed=ambiguous, alternative=alternative)


def _next_date(month: int, day: int, now: TurnNow, direction: Direction) -> date:
    today = now.today
    candidate = _safe_date(today.year, month, day)
    if direction == "past":
        return candidate if candidate <= today else _safe_date(today.year - 1, month, day)
    return candidate if candidate >= today else _safe_date(today.year + 1, month, day)


def _safe_date(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, monthrange(year, month)[1]))


def _in_n(text: str, now: TurnNow, direction: Direction) -> _Day | None:
    units = r"(\d+)\s*(day|week|fortnight|month|year)s?"
    ahead = re.fullmatch(rf"(?:in|after)\s+{units}(?:\s+(?:from now|from today|time))?", text)
    ahead = ahead or re.fullmatch(rf"{units}\s+(?:from now|from today|later|hence)", text)
    if ahead:
        return _in_n_apply(ahead, now, 1)
    back = re.fullmatch(rf"{units}\s+ago", text)
    return _in_n_apply(back, now, -1) if back else None


def _in_n_apply(match: re.Match[str], now: TurnNow, sign: int) -> _Day:
    n, unit = int(match.group(1)) * sign, match.group(2)
    delta = {
        "day": relativedelta(days=n),
        "week": relativedelta(weeks=n),
        "fortnight": relativedelta(weeks=2 * n),
        "month": relativedelta(months=n),
        "year": relativedelta(years=n),
    }[unit]
    rule = "n_units_ago" if sign < 0 else "in_n_units"
    return _Day(now.today + delta, 1, TimePrecision.DAY, rule)


def _weekday(text: str, now: TurnNow, direction: Direction) -> _Day | None:  # noqa: PLR0911
    match = re.fullmatch(rf"(?:(this|next|last|coming|past)\s+)?{_WD_RE}", text)
    if not match:
        return None
    qualifier = match.group(1)
    wd = _weekday_no(match.group("wd"))
    if wd is None:
        return None
    today = now.today
    ahead = (wd - today.weekday()) % 7
    upcoming = today + timedelta(days=ahead)
    previous = today - timedelta(days=(today.weekday() - wd) % 7 or 7)
    name = WEEKDAYS[wd].capitalize()
    if qualifier in ("last", "past"):
        return _Day(previous, 1, TimePrecision.DAY, "last_weekday")
    if qualifier == "next":
        next_week = _week_start(today) + timedelta(days=7 + wd)
        if next_week != upcoming and ahead != 0:
            return _Day(
                next_week, 1, TimePrecision.DAY, "weekday_of_next_week", True,
                f"{name} {upcoming.day} {MONTHS[upcoming.month - 1].capitalize()}, the coming one",
            )  # fmt: skip
        return _Day(
            next_week if ahead else today + timedelta(days=7),
            1,
            TimePrecision.DAY,
            "weekday_of_next_week",
        )
    if qualifier in ("this", "coming"):
        return _Day(upcoming, 1, TimePrecision.DAY, "this_weekday")
    if direction == "past":
        if ahead == 0:
            return _Day(
                today - timedelta(days=7), 1, TimePrecision.DAY, "past_weekday", True, "today"
            )
        return _Day(previous, 1, TimePrecision.DAY, "past_weekday")
    if ahead == 0:
        return _Day(
            today + timedelta(days=7), 1, TimePrecision.DAY, "bare_weekday_today", True, "today"
        )
    return _Day(upcoming, 1, TimePrecision.DAY, "upcoming_weekday")


def _periods(text: str, now: TurnNow, direction: Direction) -> _Day | None:
    today = now.today
    match = re.fullmatch(r"(this|next|last|coming|past)\s+(week|weekend|month|year)", text)
    if match is None:
        return None
    which, unit = match.group(1), match.group(2)
    step = {"this": 0, "coming": 1, "next": 1, "last": -1, "past": -1}[which]
    if unit == "week":
        start = _week_start(today) + timedelta(weeks=step)
        return _Day(start, 7, TimePrecision.DAY, f"{which}_week")
    if unit == "weekend":
        saturday = _week_start(today) + timedelta(days=5)
        if which == "this" and today.weekday() == 6:
            saturday = today - timedelta(days=1)
        if which == "coming":
            step = 0 if today.weekday() < 5 else 1
        return _Day(saturday + timedelta(weeks=step), 2, TimePrecision.DAY, f"{which}_weekend")
    if unit == "month":
        first = _month_start(today.year, today.month) + relativedelta(months=step)
        return _Day(first, 1, TimePrecision.MONTH, f"{which}_month")
    return _Day(date(today.year + step, 1, 1), 1, TimePrecision.YEAR, f"{which}_year")


def _edges(text: str, now: TurnNow, direction: Direction) -> _Day | None:  # noqa: PLR0911
    today = now.today
    match = re.fullmatch(
        r"(end|start|beginning)\s+(?:of\s+)?(?:the\s+)?(?:(this|next|last)\s+)?(week|month|year)",
        text,
    )
    if not match:
        return None
    edge, which, unit = match.group(1), match.group(2) or "this", match.group(3)
    step = {"this": 0, "next": 1, "last": -1}[which]
    if unit == "week":
        monday = _week_start(today) + timedelta(weeks=step)
        if edge == "end":
            sunday = monday + timedelta(days=6)
            friday = monday + timedelta(days=4)
            return _Day(
                sunday, 1, TimePrecision.DAY, f"end_of_{which}_week", True, f"Friday {friday.day}"
            )
        return _Day(monday, 1, TimePrecision.DAY, f"start_of_{which}_week")
    if unit == "month":
        first = _month_start(today.year, today.month) + relativedelta(months=step)
        if edge == "end":
            last = first + relativedelta(months=1) - timedelta(days=1)
            return _Day(last, 1, TimePrecision.DAY, f"end_of_{which}_month")
        return _Day(first, 1, TimePrecision.DAY, f"start_of_{which}_month")
    year = today.year + step
    if edge == "end":
        return _Day(date(year, 12, 31), 1, TimePrecision.DAY, f"end_of_{which}_year")
    return _Day(date(year, 1, 1), 1, TimePrecision.DAY, f"start_of_{which}_year")


def _day_of_month(text: str, now: TurnNow, direction: Direction) -> _Day | None:  # noqa: PLR0911
    today = now.today
    rel = re.fullmatch(r"(\d{1,2})\s+(this|next|last)\s+month", text)
    if rel:
        day_no = int(rel.group(1))
        step = {"this": 0, "next": 1, "last": -1}[rel.group(2)]
        first = _month_start(today.year, today.month) + relativedelta(months=step)
        return _clamped(first, day_no, f"day_of_{rel.group(2)}_month")
    dm = re.fullmatch(rf"(\d{{1,2}})\s+{_MONTH_RE}(?:\s+(\d{{4}}))?", text)
    md = re.fullmatch(rf"{_MONTH_RE}\s+(\d{{1,2}})(?:\s+(\d{{4}}))?", text)
    match = dm or md
    if match is not None:
        month_no = _month_no(match.group("month"))
        if month_no is None:
            return None
        day_no = int(match.group(1) if dm else match.group(2))
        year = match.group(3)
        if day_no > monthrange(today.year, month_no)[1] and not (month_no == 2 and day_no == 29):
            return None
        if year:
            return _Day(
                _safe_date(int(year), month_no, day_no), 1, TimePrecision.DAY, "day_month_year"
            )
        d = _next_date(month_no, day_no, now, direction)
        return _Day(
            d, 1, TimePrecision.DAY, "day_month_" + ("past" if direction == "past" else "next")
        )
    bare = re.fullmatch(r"(\d{1,2})", text)
    if bare and 1 <= int(bare.group(1)) <= 31:
        day_no = int(bare.group(1))
        this_month = _month_start(today.year, today.month)
        if direction == "past":
            first = this_month if day_no <= today.day else this_month - relativedelta(months=1)
            return _clamped(first, day_no, "bare_day_of_month_past")
        if day_no >= today.day:
            if day_no > monthrange(today.year, today.month)[1]:
                nxt = this_month + relativedelta(months=1)
                return _clamped(this_month, day_no, "bare_day_of_month", alt_first=nxt)
            return _Day(
                date(today.year, today.month, day_no), 1, TimePrecision.DAY, "bare_day_of_month"
            )
        return _clamped(this_month + relativedelta(months=1), day_no, "bare_day_of_month")
    return None


def _clamped(first: date, day_no: int, rule: str, alt_first: date | None = None) -> _Day:
    last = monthrange(first.year, first.month)[1]
    if day_no <= last:
        return _Day(first.replace(day=day_no), 1, TimePrecision.DAY, rule)
    month_name = MONTHS[first.month - 1].capitalize()
    alternative = f"{month_name} has {last} days"
    if alt_first is not None:
        alternative = (
            f"{day_no} {MONTHS[alt_first.month - 1].capitalize()} ({month_name} has {last} days)"
        )
    return _Day(first.replace(day=last), 1, TimePrecision.DAY, rule + "_clamped", True, alternative)


def _month(text: str, now: TurnNow, direction: Direction) -> _Day | None:  # noqa: PLR0911
    today = now.today
    match = re.fullmatch(
        rf"(?:(this|next|last|coming|early|mid|late)\s+)?{_MONTH_RE}(?:\s+(\d{{4}}))?", text
    )
    if not match:
        return None
    qualifier, year = match.group(1), match.group(3)
    month_no = _month_no(match.group("month"))
    if month_no is None:
        return None
    if year:
        return _Day(date(int(year), month_no, 1), 1, TimePrecision.MONTH, "month_year")
    this_year = date(today.year, month_no, 1)
    if qualifier == "last":
        first = this_year if month_no < today.month else date(today.year - 1, month_no, 1)
        return _Day(first, 1, TimePrecision.MONTH, "last_month_name")
    if qualifier == "this":
        return _Day(this_year, 1, TimePrecision.MONTH, "this_month_name")
    if qualifier == "next":
        if month_no > today.month:
            alt = f"{MONTHS[month_no - 1].capitalize()} {today.year + 1}"
            return _Day(this_year, 1, TimePrecision.MONTH, "next_month_name", True, alt)
        return _Day(date(today.year + 1, month_no, 1), 1, TimePrecision.MONTH, "next_month_name")
    if direction == "past":
        first = this_year if month_no <= today.month else date(today.year - 1, month_no, 1)
        return _Day(first, 1, TimePrecision.MONTH, "month_name_past")
    first = this_year if month_no >= today.month else date(today.year + 1, month_no, 1)
    return _Day(first, 1, TimePrecision.MONTH, "month_name_next")


def _year(text: str, now: TurnNow, direction: Direction) -> _Day | None:
    match = re.fullmatch(r"(\d{4})", text)
    if match and 1900 <= int(match.group(1)) <= 2200:
        return _Day(date(int(match.group(1)), 1, 1), 1, TimePrecision.YEAR, "year")
    return None


_SPAN_RULES: tuple[Callable[[str, TurnNow, Direction], _Day | None], ...] = (
    _relative_day,
    _iso_or_numeric,
    _in_n,
    _weekday,
    _periods,
    _edges,
    _day_of_month,
    _month,
    _year,
)


# ------------------------------------------------------------------ routines, as RRULEs


def _routine(text: str, now: TurnNow) -> Resolved | None:  # noqa: PLR0912, PLR0915
    """Recurring expressions -> RFC 5545 RRULE, starting at the next occurrence."""
    if not re.search(
        r"\b(every|each|daily|weekly|monthly|yearly|annually|weekdays|weekends|mondays|tuesdays|wednesdays|thursdays|fridays|saturdays|sundays)\b",
        text,
    ) and not re.search(
        r"\b(first|second|third|fourth|last)\s+\w+day\s+of\s+(the|every|each)\s+month\b", text
    ):
        return None
    body, ct, part = _clock_time(text)
    if ct is None and part is not None:
        ct = _ClockTime(time(PARTS[part][0]))
    body = re.sub(r"\b(on|at|the|of)\b", " ", body)
    body = " ".join(body.split())
    by_time = ""
    if ct is not None:
        by_time = f";BYHOUR={ct.value.hour};BYMINUTE={ct.value.minute}"
    rule: str | None = None
    name = "routine"
    interval = 1
    if re.search(r"\bevery other\b", body):
        interval = 2
    n_weeks = re.search(r"\bevery\s+(\d+)\s+weeks?\b", body)
    if n_weeks:
        interval = int(n_weeks.group(1))
    ordinal = re.search(
        r"\b(first|second|third|fourth|last)\s+(\w+day)\s+(?:every|each)?\s*month\b", body
    )
    if ordinal:
        wd = _weekday_no(ordinal.group(2))
        if wd is not None:
            n = _ORDINAL_WORDS[ordinal.group(1)]
            rule, name = f"FREQ=MONTHLY;BYDAY={n}{_WD_RRULE[wd]}", "routine_nth_weekday_of_month"
    if rule is None:
        yearly = re.search(
            rf"(?:every|each)?\s*(?:year\s+)?(\d{{1,2}})\s+{_MONTH_RE}", body
        ) or re.search(rf"(?:every|each)\s+(?:year\s+)?{_MONTH_RE}\s+(\d{{1,2}})", body)
        if yearly and ("year" in body or "annual" in body or "every" in body or "each" in body):
            groups = [g for g in yearly.groups() if g]
            day_str = next(g for g in groups if g.isdigit())
            month_no = _month_no(yearly.group("month"))
            if month_no is not None:
                rule = f"FREQ=YEARLY;BYMONTH={month_no};BYMONTHDAY={int(day_str)}"
                name = "routine_yearly_date"
    if rule is None:
        monthly_day = (
            re.search(r"\b(\d{1,2})\s+(?:every|each)\s+month\b", body)
            or re.search(r"\b(?:every|each)\s+month\s+(\d{1,2})\b", body)
            or re.search(r"\bmonthly\s+(\d{1,2})\b", body)
        )
        if monthly_day:
            rule = f"FREQ=MONTHLY;BYMONTHDAY={int(monthly_day.group(1))}"
            name = "routine_day_of_month"
    if rule is None:
        days = [_weekday_no(m.group("wd")) for m in re.finditer(_WD_RE, body)]
        days_clean = sorted({d for d in days if d is not None})
        if re.search(r"\bweekdays?\b", body):
            days_clean = [0, 1, 2, 3, 4]
        elif re.search(r"\bweekends?\b", body):
            days_clean = [5, 6]
        if days_clean:
            by_day = ",".join(_WD_RRULE[d] for d in days_clean)
            rule = f"FREQ=WEEKLY;BYDAY={by_day}"
            name = "routine_weekly"
        elif re.search(
            r"\b(daily|every day|each day|every morning|every evening|every night)\b", text
        ):
            rule, name = "FREQ=DAILY", "routine_daily"
        elif re.search(r"\b(weekly|every week|each week)\b", body):
            rule, name = f"FREQ=WEEKLY;BYDAY={_WD_RRULE[now.today.weekday()]}", "routine_weekly"
        elif re.search(r"\b(monthly|every month|each month)\b", body):
            rule, name = f"FREQ=MONTHLY;BYMONTHDAY={now.today.day}", "routine_monthly"
        elif re.search(r"\b(yearly|annually|every year|each year)\b", body):
            rule = f"FREQ=YEARLY;BYMONTH={now.today.month};BYMONTHDAY={now.today.day}"
            name = "routine_yearly"
    if rule is None:
        return None
    if interval > 1 and "INTERVAL" not in rule:
        rule += f";INTERVAL={interval}"
    rule += by_time
    first = _first_occurrence(rule, now)
    return Resolved(
        expression="",
        clock=TimeClock.OCCURRED,
        start=first,
        end=None,
        precision=TimePrecision.DATETIME if ct is not None else TimePrecision.DAY,
        rule=name,
        rrule=rule,
        assumed=bool(ct and ct.assumed),
        alternative=(
            f"{ct.alternative.strftime('%H:%M')} instead of {ct.value.strftime('%H:%M')}"
            if ct and ct.assumed and ct.alternative
            else None
        ),
        part_of_day=part,
    )


def _first_occurrence(rule: str, now: TurnNow) -> datetime:
    """The first occurrence at or after today (local), as a UTC instant."""
    local_start = datetime.combine(now.today, time(0), tzinfo=ZoneInfo(now.timezone))
    parsed = rrulestr(rule, dtstart=local_start.replace(tzinfo=None))
    after = (
        parsed.after(now.local.replace(tzinfo=None), inc=True)
        if "BYHOUR" in rule
        else parsed.after(local_start.replace(tzinfo=None), inc=True)
    )
    if after is None:
        raise UnresolvableTimeError(f"routine {rule} never occurs")
    return after.replace(tzinfo=ZoneInfo(now.timezone)).astimezone(UTC)


def validate_rrule(rule: str, start: datetime) -> str:
    """Raise ValueError unless ``rule`` is a valid RFC 5545 RRULE (validated on write)."""
    rrulestr(rule.removeprefix("RRULE:"), dtstart=start)
    return rule.removeprefix("RRULE:")


def _yearly(result: Resolved, now: TurnNow) -> Resolved:
    """A recurring date (a birthday): yearly on that day, or in that month when the day isn't
    known, from its next occurrence."""
    if result.rrule is not None:
        return result
    local = result.start.astimezone(ZoneInfo(now.timezone))
    if result.precision is TimePrecision.MONTH:
        return replace(
            result, rrule=f"FREQ=YEARLY;BYMONTH={local.month}", rule=result.rule + "+yearly"
        )
    if result.precision not in (TimePrecision.DAY, TimePrecision.DATETIME):
        return result
    rule = f"FREQ=YEARLY;BYMONTH={local.month};BYMONTHDAY={local.day}"
    start = result.start
    if local.date() < now.today:
        start = _at(_safe_date(local.year + 1, local.month, local.day), local.time(), now.timezone)
    return replace(result, rrule=rule, start=start, rule=result.rule + "+yearly")


# ------------------------------------------------------------------ reminders


@dataclass(frozen=True, slots=True)
class Lead:
    delta: timedelta
    rule: str


def resolve_lead(expression: str) -> Lead:
    """ "2 hours before", "the day before", "a week before" -> how long before the event."""
    text = _norm(re.sub(r"\bhalf an? hour\b", "30 minutes", expression, flags=re.IGNORECASE))
    text = re.sub(r"^(remind me|reminder)\s+", "", text)
    if re.fullmatch(r"(the\s+)?(day|night|evening)\s+before", text):
        return Lead(timedelta(days=1), "day_before")
    if re.fullmatch(r"(the\s+)?morning\s+of", text) or text in ("on the day", "same day"):
        return Lead(timedelta(0), "same_day")
    match = re.fullmatch(
        r"(\d+)\s*(min|minute|hour|hr|day|week)s?\s+(before|earlier|ahead|prior)", text
    )
    if match:
        n, unit = float(match.group(1)), match.group(2)
        delta = {
            "min": timedelta(minutes=n),
            "minute": timedelta(minutes=n),
            "hour": timedelta(hours=n),
            "hr": timedelta(hours=n),
            "day": timedelta(days=n),
            "week": timedelta(weeks=n),
        }[unit]
        return Lead(delta, "n_units_before")
    raise UnresolvableTimeError(f"no lead-time rule understood {expression!r}")


REMINDER_HOUR = 9  # a reminder for a day-precision event fires at 09:00 local


def reminder_time(
    event: Resolved | datetime,
    precision: TimePrecision,
    lead: timedelta,
    timezone: str,
) -> datetime:
    """When the time trigger fires: the event minus the lead. A whole-day (or coarser) event
    has no hour, so its reminder fires at 09:00 local on the day the lead points to."""
    start = event.start if isinstance(event, Resolved) else event
    if precision is TimePrecision.DATETIME:
        return start - lead
    local_day = start.astimezone(ZoneInfo(timezone)).date()
    days = math.ceil(lead / timedelta(days=1))
    return _at(local_day - timedelta(days=days), time(REMINDER_HOUR), timezone)


# ------------------------------------------------------------------ recall windows (S3.4)

WindowDirection = Literal["past", "future", "any"]

# Wider than PARTS (which are typical event times): a window should hold anything said to
# have happened in that part of the day.
_WINDOW_PARTS = {"morning": (5, 12), "afternoon": (12, 17), "evening": (17, 21), "night": (21, 29)}
_OPEN_ENDED = re.compile(r"^(ever|all time|any ?time|always)$")
_UPCOMING = re.compile(r"^(upcoming|coming up|soon|ahead|in the future|later)$")
_RECENT = re.compile(r"^(recently|lately|of late|these days)$")
_SPAN_UNITS = {"day": 1, "week": 7, "fortnight": 14, "month": 30, "year": 365}
_LOOKUP: dict[WindowDirection, Direction] = {"past": "past", "future": "future", "any": "auto"}


@dataclass(frozen=True, slots=True)
class Window:
    """A half-open span ``[start, end)`` a recall filter uses; ``None`` is unbounded."""

    expression: str
    clock: TimeClock
    start: datetime | None
    end: datetime | None
    precision: TimePrecision
    rule: str
    anchor: str | None = None
    assumed: bool = False
    alternative: str | None = None

    def contains(self, instant: datetime) -> bool:
        return (self.start is None or instant >= self.start) and (
            self.end is None or instant < self.end
        )

    def overlaps(self, start: datetime, end: datetime | None) -> bool:
        """Whether ``[start, end)`` (a point when ``end`` is None) meets this window."""
        if end is None or end <= start:
            return self.contains(start)
        return (self.end is None or start < self.end) and (self.start is None or end > self.start)


def resolve_window(
    expression: str,
    clock: TimeClock,
    now: TurnNow,
    *,
    direction: WindowDirection = "any",
    anchor: tuple[datetime, datetime | None] | None = None,
    anchor_label: str | None = None,
) -> Window:
    """A recall time expression -> ``[start, end)`` at the expression's precision.

    ``anchor`` is the span of an event the expression is relative to ("the weekend before
    **Goa**": the caller looks the Goa trip up first). ``direction`` clips a window that
    straddles now: ``past`` ends it at now, ``future`` starts it at now. Raises
    :class:`UnresolvableTimeError` when no rule understands the expression.
    """
    text = _norm(expression)
    text = re.sub(
        r"^(in|during|over|within|for|from|on|at)\s+(?=the\s|last|past|next|this)", "", text
    )
    if anchor is not None:
        window = _anchored(text, now, anchor)
    else:
        window = (
            _range(text, now, direction)
            or _relative_span(text, now)
            or _calendar(text, now, direction)
        )
    if window is None:
        raise UnresolvableTimeError(f"no window rule understood {expression!r}")
    start, end, precision, rule = window
    start, end = _clip(start, end, now.instant, direction)
    return Window(
        expression=expression,
        clock=clock,
        start=start,
        end=end,
        precision=precision,
        rule=rule,
        anchor=anchor_label,
    )


_Span = tuple[datetime | None, datetime | None, TimePrecision, str]


def _clip(
    start: datetime | None, end: datetime | None, now: datetime, direction: WindowDirection
) -> tuple[datetime | None, datetime | None]:
    straddles = (start is None or start < now) and (end is None or end > now)
    if direction == "past" and straddles:
        return start, now
    if direction == "future" and straddles:
        return now, end
    return start, end


def _day_span(d: _Day, tz: str) -> tuple[datetime, datetime]:
    start = _at(d.start, time(0), tz)
    if d.precision is TimePrecision.MONTH:
        return start, _at(d.start + relativedelta(months=1), time(0), tz)
    if d.precision is TimePrecision.YEAR:
        return start, _at(date(d.start.year + 1, 1, 1), time(0), tz)
    return start, _at(d.start + timedelta(days=d.days), time(0), tz)


def _calendar(text: str, now: TurnNow, direction: WindowDirection) -> _Span | None:
    """One calendar span: a day, week, weekend, month, year, or a part of today."""
    if _OPEN_ENDED.match(text):
        return None, None, TimePrecision.YEAR, "open"
    if _UPCOMING.match(text):
        return now.instant, now.instant + timedelta(days=30), TimePrecision.DAY, "upcoming_30_days"
    if _RECENT.match(text):
        return now.instant - timedelta(days=30), now.instant, TimePrecision.DAY, "recent_30_days"
    part = re.fullmatch(r"(?:(this|yesterday|today|tomorrow)\s+)?(morning|afternoon|evening|night)"
                        r"|(tonight|last night)", text)  # fmt: skip
    if part:
        return _part_of_day(part, now)
    day = _calendar_day(text, now, _LOOKUP[direction])
    if day is None:
        return None
    if direction == "any" and day.rule.startswith(("day_month_", "month_name_")):
        # No direction given and no year: the nearer of the last and the next occurrence.
        past = _calendar_day(text, now, "past")
        future = _calendar_day(text, now, "future")
        if past is not None and future is not None:
            day = min(past, future, key=lambda d: _distance(_day_span(d, now.timezone), now))
    start, end = _day_span(day, now.timezone)
    return start, end, day.precision, day.rule


def _calendar_day(text: str, now: TurnNow, direction: Direction) -> _Day | None:
    day = _span(text, now, direction)
    if day is None:
        bare = re.sub(r"^(on|at|in the|in|the|around|about|during)\s+", "", text)
        day = _span(bare, now, direction) or _weekday_and_date(bare, now, direction)
    return day


def _distance(span: tuple[datetime, datetime], now: TurnNow) -> timedelta:
    start, end = span
    if start <= now.instant < end:
        return timedelta(0)
    return min(abs(start - now.instant), abs(end - now.instant))


def _part_of_day(match: re.Match[str], now: TurnNow) -> _Span:
    today = now.today
    if match.group(3):
        day = today if match.group(3) == "tonight" else today - timedelta(days=1)
        part = "night"
    else:
        word = match.group(1) or "this"
        offset = {"this": 0, "today": 0, "yesterday": -1, "tomorrow": 1}[word]
        day, part = today + timedelta(days=offset), match.group(2)
    first, last = _WINDOW_PARTS[part]
    start = _at(day, time(0), now.timezone) + timedelta(hours=first)
    end = _at(day, time(0), now.timezone) + timedelta(hours=last)
    return start, end, TimePrecision.DATETIME, f"part_of_day_{part}"


def _relative_span(text: str, now: TurnNow) -> _Span | None:
    """ "the last 7 days", "past two weeks", "the next 30 days": counted from now."""
    match = re.fullmatch(
        r"(?:the\s+)?(last|past|previous|next|coming|following)\s+(?:(\d+)\s+)?"
        r"(day|week|fortnight|month|year)s?",
        text,
    )
    if match is None or (match.group(2) is None and match.group(1) in ("last", "next", "coming")):
        # "last week" / "next month" are calendar periods, handled by _calendar.
        return None
    n = int(match.group(2) or 1)
    unit = match.group(3)
    delta = timedelta(days=n * _SPAN_UNITS[unit])
    if match.group(1) in ("next", "coming", "following"):
        return now.instant, now.instant + delta, TimePrecision.DAY, f"next_{n}_{unit}s"
    return now.instant - delta, now.instant, TimePrecision.DAY, f"last_{n}_{unit}s"


def _range(text: str, now: TurnNow, direction: WindowDirection) -> _Span | None:  # noqa: PLR0911
    """Bounds: "since X", "before X", "after X", "until X", "between X and Y", "from X to Y"."""
    between = re.fullmatch(r"(?:between|from)\s+(.+?)\s+(?:and|to|until|till)\s+(.+)", text)
    if between:
        left, right = between.group(1), between.group(2)
        if re.fullmatch(r"\d{1,2}", left) and re.search(r"[a-z]", right):
            # "between 1 and 7 September": the month is written once, on the right.
            month = re.sub(r"^\d{1,2}\s+", "", right)
            left = f"{left} {month}"
        a = _calendar(left, now, direction)
        b = _calendar(right, now, direction)
        if a is None or b is None or a[0] is None:
            return None
        return a[0], b[1], min(a[2], b[2], key=_PRECISION_ORDER.index), "between"
    bound = re.fullmatch(r"(since|before|after|until|till|up to|by)\s+(.+)", text)
    if not bound:
        return None
    word, rest = bound.group(1), bound.group(2)
    span = _calendar(rest, now, direction)
    if span is None:
        return None
    start, end, precision, rule = span
    if word == "since":
        return (
            start,
            now.instant if (start is None or start < now.instant) else None,
            precision,
            f"since_{rule}",
        )
    if word == "before":
        return None, start, precision, f"before_{rule}"
    if word == "after":
        return end, None, precision, f"after_{rule}"
    # until / by: from now (looking ahead) or from the beginning (looking back) to its end
    first = now.instant if direction == "future" else None
    return first, end, precision, f"until_{rule}"


_PRECISION_ORDER = [
    TimePrecision.DATETIME,
    TimePrecision.DAY,
    TimePrecision.MONTH,
    TimePrecision.YEAR,
]


def _anchored(  # noqa: PLR0911
    text: str, now: TurnNow, anchor: tuple[datetime, datetime | None]
) -> _Span | None:
    """A window relative to an event: "the weekend before", "the week after", "since"."""
    tz = now.timezone
    a_start, a_end = anchor
    first_day = a_start.astimezone(ZoneInfo(tz)).date()
    end_instant = (
        a_end
        if a_end is not None and a_end > a_start
        else _at(first_day + timedelta(days=1), time(0), tz)
    )
    last_day = (end_instant - timedelta(microseconds=1)).astimezone(ZoneInfo(tz)).date()
    text = re.sub(r"\s+(it|that|then|this)$", "", text)
    if text in ("", "during", "at", "on", "when", "in", "at the time", "the same time"):
        return a_start, end_instant, TimePrecision.DAY, "anchor_during"
    if text in ("before", "earlier", "prior"):
        return None, a_start, TimePrecision.DAY, "anchor_before"
    if text in ("after", "later", "afterwards"):
        return end_instant, None, TimePrecision.DAY, "anchor_after"
    if text == "since":
        return a_start, now.instant, TimePrecision.DAY, "anchor_since"
    match = re.fullmatch(
        r"(?:the\s+)?(?:(\d+)\s+)?(day|days|week|weeks|weekend|month)\s+(before|after)", text
    )
    if match is None:
        return None
    n = int(match.group(1) or 1)
    unit, side = match.group(2).rstrip("s"), match.group(3)
    if unit == "weekend":
        if side == "before":
            back = (first_day.weekday() - 5) % 7 or 7
            saturday = first_day - timedelta(days=back)
        else:
            ahead = (5 - last_day.weekday()) % 7 or 7
            saturday = last_day + timedelta(days=ahead)
        start = _at(saturday, time(0), tz)
        return start, start + timedelta(days=2), TimePrecision.DAY, f"anchor_weekend_{side}"
    days = {"day": 1, "week": 7, "month": 30}[unit] * n
    if side == "before":
        start_day = first_day - timedelta(days=days)
        return (
            _at(start_day, time(0), tz),
            _at(first_day, time(0), tz),
            TimePrecision.DAY,
            (f"anchor_{unit}_before"),
        )
    after_day = last_day + timedelta(days=1)
    return (
        _at(after_day, time(0), tz),
        _at(after_day + timedelta(days=days), time(0), tz),
        (TimePrecision.DAY),
        f"anchor_{unit}_after",
    )

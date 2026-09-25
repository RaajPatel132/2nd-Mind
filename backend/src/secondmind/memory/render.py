"""Verbalised keys (S2.14, ADR-0020): one plain sentence per memory, rendered from its structured
fields after resolution. Pure: no model call, no I/O.

Rules: first person (queries are first person); absolute calendar words only (weekday, date,
month, year, part of day, "weekend"; never "yesterday" or "last week", which go stale); entity
names with labels ("Nisha (sister)"); kind and state in plain words; numbers with units written
out. The same date formatting is used for acknowledgements.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from secondmind.core import EntityRole, Kind, TimePrecision
from secondmind.memory.records import ItemContent

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_RRULE_DAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_ORDINALS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", -1: "last"}
_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
)  # fmt: skip

# Verb forms per intention subtype: (to-do phrase, past participle).
_INTENT_VERBS = {
    "watch": ("to watch", "watched"),
    "read": ("to read", "read"),
    "learn": ("to learn", "learned"),
    "buy": ("to buy", "bought"),
    "visit": ("to visit", "visited"),
    "try": ("to try", "tried"),
    "gift": ("to gift", "given"),
    "listen": ("to listen to", "listened to"),
    "play": ("to play", "played"),
    "eat": ("to eat", "eaten"),
    "cook": ("to cook", "cooked"),
}
_RESOURCE_VERBS = {"video": "watched", "image": "seen", "link": "opened"}

_UNITS = (
    (re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:mins?|minute)\b", re.I), r"\1 minutes"),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:hrs?|h|hour)\b", re.I), r"\1 hours"),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:secs?|s)\b(?!\w)", re.I), r"\1 seconds"),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s*kms?\b", re.I), r"\1 km"),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s*kgs?\b", re.I), r"\1 kg"),
    (re.compile(r"\b1 minutes\b"), "1 minute"),
    (re.compile(r"\b1 hours\b"), "1 hour"),
)

_PAST = (
    (re.compile(r"^I'm\b|^I am\b"), "I was"),
    (re.compile(r"^I live\b"), "I lived"),
    (re.compile(r"^I work\b"), "I worked"),
    (re.compile(r"^I have\b"), "I had"),
    (re.compile(r"^I like\b"), "I liked"),
    (re.compile(r"^I love\b"), "I loved"),
    (re.compile(r"^I own\b"), "I owned"),
    (re.compile(r"^I use\b"), "I used"),
    (re.compile(r"^I prefer\b"), "I preferred"),
    (re.compile(r"^I study\b"), "I studied"),
    (re.compile(r"^I drive\b"), "I drove"),
    (re.compile(r"\b(is)\b"), "was"),
    (re.compile(r"\b(are)\b"), "were"),
    (re.compile(r"\blives\b"), "lived"),
    (re.compile(r"\bworks\b"), "worked"),
    (re.compile(r"\blikes\b"), "liked"),
)


@dataclass(frozen=True, slots=True)
class RenderEntity:
    name: str
    role: EntityRole
    kind: str = "person"
    label: str | None = None
    type_label: str | None = None

    @property
    def display(self) -> str:
        if self.kind == "self":
            return "me"
        extra = self.label or self.type_label
        return (
            f"{self.name} ({extra})" if extra and extra.lower() != self.name.lower() else self.name
        )


@dataclass(frozen=True, slots=True)
class RenderInput:
    item: ItemContent
    timezone: str
    entities: Sequence[RenderEntity] = ()
    category: str | None = None
    superseded_by: ItemContent | None = None
    replaced: ItemContent | None = None
    fulfilled_by: ItemContent | None = None
    corrected_by: ItemContent | None = None
    extra: dict[str, str] = field(default_factory=dict)


# ------------------------------------------------------------------ dates


def local(dt: datetime, tz: str) -> datetime:
    return dt.astimezone(ZoneInfo(tz))


def clock_time(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    suffix = "am" if dt.hour < 12 else "pm"
    return f"{hour} {suffix}" if dt.minute == 0 else f"{hour}:{dt.minute:02d} {suffix}"


def part_of_day(dt: datetime) -> str:
    if 5 <= dt.hour < 12:
        return "morning"
    if 12 <= dt.hour < 17:
        return "afternoon"
    if 17 <= dt.hour < 21:
        return "evening"
    return "night"


def format_day(dt: datetime, tz: str, *, weekday: bool = True, year: bool = True) -> str:
    d = local(dt, tz)
    text = f"{d.day} {_MONTHS[d.month - 1]}"
    if year:
        text += f" {d.year}"
    return f"{_WEEKDAYS[d.weekday()]} {text}" if weekday else text


def format_when(
    dt: datetime, precision: TimePrecision | None, tz: str, *, part: bool = False
) -> str:
    """Absolute, honest about precision: "May 2027", "Friday 3 October 2026, 5 pm"."""
    d = local(dt, tz)
    match precision:
        case TimePrecision.YEAR:
            return str(d.year)
        case TimePrecision.MONTH:
            return f"{_MONTHS[d.month - 1]} {d.year}"
        case TimePrecision.DATETIME:
            if part:
                period = part_of_day(d)
                return f"{format_day(dt, tz)}, {'at' if period == 'night' else 'in the'} {period}"
            return f"{format_day(dt, tz)}, {clock_time(d)}"
        case _:
            return format_day(dt, tz)


def format_short(dt: datetime, precision: TimePrecision | None, tz: str, now: datetime) -> str:
    """For acknowledgements: "Sat 3 Oct", "Fri 2 Oct, 5 pm", "May 2027"."""
    d = local(dt, tz)
    n = local(now, tz)
    if precision is TimePrecision.YEAR:
        return str(d.year)
    if precision is TimePrecision.MONTH:
        return f"{_MONTHS[d.month - 1]} {d.year}"
    text = f"{_WEEKDAYS[d.weekday()][:3]} {d.day} {_MONTHS[d.month - 1][:3]}"
    if d.year != n.year:
        text += f" {d.year}"
    if precision is TimePrecision.DATETIME:
        text += f", {clock_time(d)}"
    return text


def rrule_words(rrule: str, tz: str, start: datetime | None = None) -> str:  # noqa: PLR0911
    """RFC 5545 rule in words: "every Monday, Wednesday and Friday at 7 am"."""
    parts = dict(p.split("=", 1) for p in rrule.removeprefix("RRULE:").split(";") if "=" in p)
    freq = parts.get("FREQ", "")
    interval = int(parts.get("INTERVAL", "1"))
    every = "every other" if interval == 2 else (f"every {interval}" if interval > 2 else "every")
    days = [d for d in parts.get("BYDAY", "").split(",") if d]
    at = ""
    if "BYHOUR" in parts:
        hour = int(parts["BYHOUR"].split(",")[0])
        minute = int(parts.get("BYMINUTE", "0").split(",")[0])
        at = f" at {clock_time(datetime(2000, 1, 1, hour, minute, tzinfo=UTC))}"
    elif start is not None and freq in ("DAILY", "WEEKLY"):
        s = local(start, tz)
        if s.hour or s.minute:
            at = f" at {clock_time(s)}"
    if freq == "WEEKLY":
        names = [_WEEKDAYS[_RRULE_DAYS[d[-2:]]] for d in days] or (
            [_WEEKDAYS[local(start, tz).weekday()]] if start else ["week"]
        )
        if names == list(_WEEKDAYS[:5]):
            return f"{every} weekday{at}"
        if names == list(_WEEKDAYS[5:]):
            return f"{every} weekend{at}"
        return f"{every} {_join(names)}{at}"
    if freq == "MONTHLY":
        if days:
            match = re.match(r"^([+-]?\d+)?([A-Z]{2})$", days[0])
            if match:
                n = int(match.group(1) or parts.get("BYSETPOS", "1"))
                day = _WEEKDAYS[_RRULE_DAYS[match.group(2)]]
                return f"on the {_ORDINALS.get(n, str(n))} {day} of {every} month{at}"
        monthday = parts.get("BYMONTHDAY") or (str(local(start, tz).day) if start else "1")
        return f"on the {_ordinal(int(monthday))} of {every} month{at}"
    if freq == "YEARLY":
        month = int(parts.get("BYMONTH", str(local(start, tz).month) if start else "1"))
        if "BYMONTHDAY" not in parts and "BYMONTH" in parts:
            return f"{every} year in {_MONTHS[month - 1]}{at}"
        day_of_month = int(parts.get("BYMONTHDAY", str(local(start, tz).day) if start else "1"))
        return f"{every} year on {day_of_month} {_MONTHS[month - 1]}{at}"
    if freq == "DAILY":
        return f"{every} day{at}"
    return f"repeating ({rrule})"


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _join(words: Sequence[str]) -> str:
    words = list(words)
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


# ------------------------------------------------------------------ sentence pieces


def with_units(text: str) -> str:
    for pattern, repl in _UNITS:
        text = pattern.sub(repl, text)
    return text


def past_tense(text: str) -> str:
    for pattern, repl in _PAST:
        new = pattern.sub(repl, text, count=1)
        if new != text:
            return new
    return text


def _clause(text: str) -> str:
    return with_units(" ".join(text.split()).rstrip(" ."))


def _people(entities: Sequence[RenderEntity], *roles: EntityRole) -> list[str]:
    return [e.display for e in entities if e.role in roles and e.kind != "self"]


def _tail(
    when: datetime | None, precision: TimePrecision | None, tz: str, category: str | None
) -> str:
    bits: list[str] = []
    if when is not None and precision in (TimePrecision.DAY, TimePrecision.DATETIME, None):
        d = local(when, tz)
        if d.weekday() >= 5:
            bits.append("Weekend")
        bits.append(f"{_MONTHS[d.month - 1]} {d.year}")
    if category:
        bits.append(category.rsplit("/", 1)[-1].replace("-", " "))
    if not bits:
        return ""
    text = ", ".join(bits)
    return f" {text[0].upper()}{text[1:]}."


def _where_who(entities: Sequence[RenderEntity]) -> str:
    text = ""
    places = _people(entities, EntityRole.AT)
    if places:
        text += ", at " + _join(places)
    company = _people(entities, EntityRole.WITH)
    if company:
        text += ", with " + _join(company)
    return text


# ------------------------------------------------------------------ per kind


def render_verbal(inp: RenderInput) -> str:  # noqa: PLR0911
    item, tz = inp.item, inp.timezone
    if inp.corrected_by is not None:
        return _mistake(inp, inp.corrected_by)
    match item.kind:
        case Kind.EPISODE:
            return _episode(inp)
        case Kind.INTENTION:
            return _intention(inp)
        case Kind.PLAN:
            return _plan(inp)
        case Kind.TASK:
            return _task(inp)
        case Kind.FACT | Kind.PREFERENCE:
            return _fact(inp)
        case Kind.RESOURCE:
            verb = _RESOURCE_VERBS.get(item.format.value if item.format else "", "read")
            kind = item.format.value if item.format else "link"
            summary = f" {_clause(item.summary)}." if item.summary else ""
            added = f" Saved {format_day(item.mentioned_at, tz)}."
            if item.state == "consumed":
                return f"{verb.capitalize()} {kind}: {_clause(item.title)}.{summary}{added}"
            return f"Saved {kind}: {_clause(item.title)}.{summary}{added} Not {verb} yet."
        case Kind.NOTE:
            label = f" ({item.subtype})" if item.subtype else ""
            about = _people(inp.entities, EntityRole.ABOUT, EntityRole.WITH, EntityRole.FOR)
            about_text = f" About {_join(about)}." if about else ""
            return (
                f"Note{label}: {_clause(item.text)}.{about_text} Noted "
                f"{format_day(item.mentioned_at, tz)}.{_tail(None, None, tz, inp.category)}"
            )
        case Kind.RULE:
            return f"Rule for the assistant ({item.state}): {_clause(item.text)}."
        case _:
            return f"Pattern ({item.state}): {_clause(item.text)}."


def _noun(item: ItemContent) -> str:
    activity = item.attributes.get("activity")
    if isinstance(activity, str) and activity:
        return activity
    if item.subtype and item.subtype not in ("measurement", "experience", "completion"):
        return item.subtype.replace("_", " ")
    return item.title.lower()


def _episode(inp: RenderInput) -> str:
    item, tz = inp.item, inp.timezone
    sentence = f"Logged {_noun(item)}: {_clause(item.text)}"
    when = item.occurred_start
    if when is not None:
        if item.time_precision in (TimePrecision.MONTH, TimePrecision.YEAR):
            sentence += " in " + format_when(when, item.time_precision, tz)
        else:
            part = item.time_precision is TimePrecision.DATETIME
            sentence += " on " + format_when(when, item.time_precision, tz, part=part)
    sentence += _where_who(inp.entities)
    return sentence + "." + _tail(when, item.time_precision, tz, inp.category)


def _object(inp: RenderInput) -> str:
    works = [
        e.display
        for e in inp.entities
        if e.kind in ("work", "thing", "place", "topic", "org", "project")
        and e.role in (EntityRole.ABOUT, EntityRole.AT, EntityRole.PART_OF)
    ]
    return _join(works) if works else _clause(inp.item.title)


def _intention(inp: RenderInput) -> str:
    item, tz = inp.item, inp.timezone
    to_do, done = _INTENT_VERBS.get(item.subtype or "", (f"to {item.subtype or 'do'}", "done"))
    obj = _object(inp)
    by = _people(inp.entities, EntityRole.BY)
    recommended = f", recommended by {_join(by)}" if by else ""
    added = format_day(item.mentioned_at, tz)
    if item.state == "fulfilled":
        episode = inp.fulfilled_by
        when = ""
        if episode is not None and episode.occurred_start is not None:
            when = " on " + format_when(
                episode.occurred_start,
                episode.time_precision,
                tz,
                part=episode.time_precision is TimePrecision.DATETIME,
            )
        listed = format_day(item.mentioned_at, tz, weekday=False)
        list_name = to_do.replace(" ", "-", 1)
        return (
            f"{done.capitalize()} {obj}{when}. It was on my {list_name} list from "
            f"{listed}{recommended}."
        )
    if item.state == "dropped":
        return f"Dropped from my wish list, {to_do}: {obj}{recommended}. Added {added}."
    target = ""
    if item.due_at is not None:
        target = f" Target: {format_when(item.due_at, item.time_precision, tz)}."
    status = "In progress." if item.state == "active" else f"Not {done} yet."
    return f"Wish list, {to_do}: {obj}{recommended}. Added {added}.{target} {status}"


def _plan(inp: RenderInput) -> str:
    item, tz = inp.item, inp.timezone
    title = _clause(item.title)
    when = (
        format_when(item.occurred_start, item.time_precision, tz)
        if item.occurred_start is not None
        else "no date yet"
    )
    who = _where_who(inp.entities) + (
        ", for " + _join(_people(inp.entities, EntityRole.FOR, EntityRole.ABOUT))
        if _people(inp.entities, EntityRole.FOR, EntityRole.ABOUT)
        else ""
    )
    if item.rrule and item.state == "scheduled":
        since_at = item.valid_from or item.occurred_start
        since = f", since {format_when(since_at, TimePrecision.MONTH, tz)}" if since_at else ""
        words = rrule_words(item.rrule, tz, item.occurred_start)
        return f"Routine: {title[0].lower() + title[1:]} {words}{who}{since}."
    match item.state:
        case "happened":
            return f"Happened: {title} on {when}{who}." + _tail(
                item.occurred_start, item.time_precision, tz, inp.category
            )
        case "cancelled":
            return f"Cancelled: {title}, was on {when}{who}."
        case "moved":
            new = inp.superseded_by
            moved_to = ""
            if new is not None and new.occurred_start is not None:
                moved_to = f"; moved to {format_when(new.occurred_start, new.time_precision, tz)}"
            return f"Moved: {title} was on {when}{moved_to}."
    return f"Upcoming: {title} on {when}{who}." + _tail(
        item.occurred_start, item.time_precision, tz, inp.category
    )


def _task(inp: RenderInput) -> str:
    item, tz = inp.item, inp.timezone
    due = (
        f", due {format_when(item.due_at, item.time_precision, tz)}"
        if item.due_at is not None
        else ""
    )
    people = _people(inp.entities, EntityRole.FOR, EntityRole.WITH, EntityRole.ABOUT)
    for_whom = f", for {_join(people)}" if people else ""
    owed = _people(inp.entities, EntityRole.BY)
    owed_by = f", owed by {_join(owed)}" if owed else ""
    text = _clause(item.text)
    match item.state:
        case "done":
            return f"Done: {text}{for_whom}{owed_by}{due.replace(', due', ', was due')}."
        case "dropped":
            return f"Dropped task: {text}{for_whom}."
    return f"To do: {text}{due}{for_whom}{owed_by}. Open."


def _fact(inp: RenderInput) -> str:
    item, tz = inp.item, inp.timezone
    text = _clause(item.text)
    about = _people(inp.entities, EntityRole.ABOUT, EntityRole.OWNER)
    about_text = f" About {_join(about)}." if about else ""
    if item.state == "superseded":
        since = format_day(item.valid_to, tz, weekday=False) if item.valid_to else "recently"
        now_text = ""
        if inp.superseded_by is not None:
            now_text = f" {_clause(inp.superseded_by.text)} now."
        return f"No longer true since {since}: {past_tense(text)}.{now_text}"
    label = "Preference" if item.kind is Kind.PREFERENCE else "Fact"
    if item.valid_from is not None:
        since = f" True since {format_when(item.valid_from, item.time_precision, tz)}."
    else:
        since = f" Noted {format_day(item.mentioned_at, tz)}."
    return f"{label}: {text}.{about_text}{since}"


def _mistake(inp: RenderInput, fix: ItemContent) -> str:
    """A row corrected as a mistake (S3.12): never true, so no validity and no history."""
    on = format_day(fix.mentioned_at, inp.timezone, weekday=False)
    return (
        f"Recorded by mistake, corrected on {on}: {_clause(inp.item.text)}. "
        f"The right version: {_clause(fix.text)}."
    )


def render_change(inp: RenderInput) -> str | None:
    """The ``change`` key of an item that superseded another (a move, or a new value)."""
    old = inp.replaced
    if old is None:
        return None
    item, tz = inp.item, inp.timezone
    on = format_day(item.mentioned_at, tz, weekday=False)
    if (
        item.kind is Kind.PLAN
        and old.occurred_start is not None
        and item.occurred_start is not None
    ):
        same_year = local(old.occurred_start, tz).year == local(item.occurred_start, tz).year
        before = format_day(old.occurred_start, tz, year=not same_year)
        after = format_when(item.occurred_start, item.time_precision, tz)
        title = _clause(item.title)
        return f"Changed on {on}: {title[0].lower() + title[1:]} moved from {before} to {after}."
    return f"Changed on {on}: {_clause(item.text)}; before that, {past_tense(_clause(old.text))}."

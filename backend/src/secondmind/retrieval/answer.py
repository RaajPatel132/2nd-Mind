"""The answer (S3.8): a context pack built in code, citations mapped in code, abstention by
template.

**No arithmetic in the model.** Every date in the pack is absolute and every distance in time
("12 days ago", "in 4 days", "6 days before the Goa trip") is computed here; counts come only
from ``aggregate``. **Citations**: the reply's ``[n]`` markers are mapped to memory or turn ids;
a marker that matches no evidence is stripped while streaming and counted. **No evidence, no
model call**: when nothing survives selection, the reply is a template built from the plan's
topic, so it can't invent an answer.
"""

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from secondmind.core import Citation, EntityRole, Kind, SaveOffer, Shape, TimePrecision
from secondmind.ingestion import TurnNow
from secondmind.memory import EntityRecord, ItemRecord, format_day, format_when, local
from secondmind.retrieval.plan import SubQuery
from secondmind.retrieval.tools import Occurrence

NO_EVIDENCE = "I don't have anything saved about {topic}."
# Added after a reply that rested on something I said: conversation isn't memory until the
# person says so (S3.9). A "yes" next turn saves it.
SAVE_OFFER = "Want me to save what I suggested? Just say yes."


class AnswerVars(BaseModel):
    """Typed variables of the ``answer`` prompt (v3: the context pack goes in ``context``)."""

    now: str
    timezone: str
    context: str


@dataclass(slots=True)
class Evidence:
    """One numbered piece of evidence: a memory, or something said in a past turn."""

    marker: int
    kind: Literal["item", "turn"]
    title: str
    item: ItemRecord | None = None
    turn_id: uuid.UUID | None = None
    role: Literal["user", "assistant"] | None = None
    said: str = ""
    said_at: datetime | None = None
    soft_only: bool = False
    counted: bool = False
    look_alike: bool = False
    occurrences: list[Occurrence] = field(default_factory=list)

    @property
    def target(self) -> uuid.UUID | None:
        return self.item.id if self.item is not None else self.turn_id

    def citation(self) -> Citation:
        return Citation(
            marker=self.marker,
            kind=self.kind,
            item_id=self.item.id if self.item else None,
            turn_id=self.turn_id,
            title=self.title,
        )


@dataclass(slots=True)
class Part:
    """What one sub-query brings to the answer."""

    sub: SubQuery
    evidence: list[Evidence] = field(default_factory=list)
    count: float | None = None
    count_note: str = ""
    more: int = 0
    relaxed: str = ""
    anchor: tuple[str, datetime] | None = None

    @property
    def empty(self) -> bool:
        return not self.evidence and self.count is None


# ------------------------------------------------------------------ durations, in code


def days_between(a: date, b: date) -> int:
    return (b - a).days


def relative(when: datetime, now: TurnNow) -> str:
    """ "today", "yesterday", "12 days ago", "in 4 days", "3 weeks ago", "5 months ago"."""
    days = days_between(now.today, local(when, now.timezone).date())
    if days == 0:
        return "today"
    if days == -1:
        return "yesterday"
    if days == 1:
        return "tomorrow"
    n = abs(days)
    if n < 14:
        amount = f"{n} days"
    elif n < 60:
        amount = f"{round(n / 7)} weeks"
    elif n < 730:
        amount = f"{round(n / 30.4)} months"
    else:
        amount = f"{round(n / 365.25)} years"
    return f"{amount} ago" if days < 0 else f"in {amount}"


def apart(when: datetime, anchor: datetime, label: str, tz: str) -> str:
    """ "6 days before the Goa trip with Kabir"."""
    days = days_between(local(anchor, tz).date(), local(when, tz).date())
    if days == 0:
        return f"the same day as {label}"
    n = abs(days)
    unit = "day" if n == 1 else "days"
    return f"{n} {unit} {'before' if days < 0 else 'after'} {label}"


# ------------------------------------------------------------------ the pack


def evidence_line(
    ev: Evidence,
    *,
    now: TurnNow,
    entities: Mapping[uuid.UUID, EntityRecord],
    roles: Mapping[uuid.UUID, Sequence[tuple[uuid.UUID, EntityRole]]],
    anchor: tuple[str, datetime] | None = None,
) -> str:
    tz = now.timezone
    if ev.kind == "turn":
        who = "I said" if ev.role == "assistant" else "You said"
        when = format_day(ev.said_at, tz) if ev.said_at else "earlier"
        ago = f" ({relative(ev.said_at, now)})" if ev.said_at else ""
        return (
            f"[{ev.marker}] {who} on {when}{ago}, in the chat (a conversation turn, not a saved "
            f'memory): "{ev.said}"'
        )
    item = ev.item
    assert item is not None  # noqa: S101 - item evidence always has its row
    bits = [f"[{ev.marker}] {_kind(item)}", _status(item, now)]
    bits.append(f'"{" ".join(item.text.split())}"')
    bits.extend(_times(item, now, anchor))
    who = _who(item, entities, roles)
    if who:
        bits.append(who)
    if ev.occurrences:
        shown = ", ".join(
            format_when(o.start, TimePrecision.DATETIME, tz) + f" ({relative(o.start, now)})"
            for o in ev.occurrences[:6]
        )
        bits.append(f"occurrences in the window: {shown}")
    if ev.counted:
        bits.append("counted")
    if ev.look_alike:
        bits.append("looks like one of the counted things but wasn't filed as one; not counted")
    if ev.soft_only:
        bits.append("found by the soft channel only")
    return " · ".join(b for b in bits if b)


def build_pack(
    parts: Sequence[Part],
    *,
    now: TurnNow,
    entities: Mapping[uuid.UUID, EntityRecord],
    roles: Mapping[uuid.UUID, Sequence[tuple[uuid.UUID, EntityRole]]],
    notes_after: Sequence[str] = (),
) -> str:
    tz = now.timezone
    lines = [f"Now: {format_when(now.instant, TimePrecision.DATETIME, tz)} ({tz})."]
    for n, part in enumerate(parts, start=1):
        sub = part.sub
        lines += ["", f"Question {n} ({sub.shape.value}): {sub.question}"]
        for trace in sub.times:
            lines.append(
                f"Window: '{trace.expression}' = {trace.value} to {trace.end} ({trace.clock.value})"
                + (f", relative to {trace.anchor}" if trace.anchor else "")
            )
        if part.relaxed:
            lines.append(f"Relaxed: {part.relaxed}")
        if part.empty:
            lines.append("Evidence: none. Say plainly: " + NO_EVIDENCE.format(topic=sub.topic))
            continue
        if part.count is not None:
            label = _count_value(part.count)
            lines.append(
                f"Exact result ({sub.aggregate.op if sub.aggregate else 'count'}, from the "
                f"database): {label}. Use this number as it is."
            )
        lines.append("Evidence:")
        lines += [
            evidence_line(e, now=now, entities=entities, roles=roles, anchor=part.anchor)
            for e in part.evidence
        ]
        if part.more:
            lines.append(f"...and {part.more} more not listed (say so).")
        if part.count_note:
            lines.append(f"Not counted: {part.count_note} (the offer is added after your reply)")
    if notes_after:
        lines += ["", "Notes added after your reply (don't repeat them): " + " ".join(notes_after)]
    return "\n".join(lines)


def _count_value(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def _kind(item: ItemRecord) -> str:
    kind = item.kind.value
    if item.rrule:
        kind = "routine"
    if item.subtype:
        kind += f" ({item.subtype})"
    if item.format:
        kind += f" ({item.format.value})"
    return kind


def _status(item: ItemRecord, now: TurnNow) -> str:  # noqa: PLR0911
    tz = now.timezone
    ended = item.valid_to is not None and item.kind in (Kind.FACT, Kind.PREFERENCE)
    if item.state == "superseded" or ended:
        until = format_day(item.valid_to, tz) if item.valid_to else "recently"
        return f"no longer true (until {until})"
    match item.kind:
        case Kind.FACT | Kind.PREFERENCE:
            return "current"
        case Kind.PLAN:
            if item.state == "scheduled" and item.occurred_start is not None and not item.rrule:
                past = item.occurred_start < now.instant
                return "scheduled, now past" if past else "upcoming"
            return {"moved": "moved (replaced by a later plan)"}.get(item.state, item.state)
        case Kind.INTENTION:
            return {
                "wanted": "on the wish list",
                "active": "in progress",
                "fulfilled": "done",
                "dropped": "dropped",
            }[item.state]
        case Kind.RESOURCE:
            return "saved, not read yet" if item.state == "saved" else "read"
        case Kind.EPISODE:
            return "happened"
    return item.state


def _times(item: ItemRecord, now: TurnNow, anchor: tuple[str, datetime] | None) -> list[str]:
    tz = now.timezone
    out: list[str] = []
    if item.occurred_start is not None and not item.rrule:
        part = item.time_precision is TimePrecision.DATETIME
        when = format_when(item.occurred_start, item.time_precision, tz, part=part)
        tense = "past" if item.occurred_start < now.instant else "upcoming"
        rel = relative(item.occurred_start, now)
        extra = f", {apart(item.occurred_start, anchor[1], anchor[0], tz)}" if anchor else ""
        out.append(f"on {when} ({rel}, {tense}{extra})")
    if item.due_at is not None:
        due = format_when(item.due_at, item.time_precision, tz)
        out.append(f"due {due} ({relative(item.due_at, now)})")
    if item.valid_from is not None and item.kind in (Kind.FACT, Kind.PREFERENCE, Kind.PLAN):
        since = format_when(item.valid_from, item.time_precision, tz)
        out.append(f"since {since} ({relative(item.valid_from, now)})")
    if not out:
        out.append(
            f"noted {format_day(item.mentioned_at, tz)} ({relative(item.mentioned_at, now)})"
        )
    return out


def _who(
    item: ItemRecord,
    entities: Mapping[uuid.UUID, EntityRecord],
    roles: Mapping[uuid.UUID, Sequence[tuple[uuid.UUID, EntityRole]]],
) -> str:
    words = {
        EntityRole.WITH: "with",
        EntityRole.AT: "at",
        EntityRole.FOR: "for",
        EntityRole.BY: "by",
        EntityRole.ABOUT: "about",
        EntityRole.OWNER: "owned by",
        EntityRole.PART_OF: "part of",
    }
    bits = []
    for entity_id, role in roles.get(item.id, ()):
        entity = entities.get(entity_id)
        if entity is not None and entity.display != "me":
            bits.append(f"{words[role]} {entity.display}")
    return ", ".join(bits)


# ------------------------------------------------------------------ citations, while streaming

_MARKER = re.compile(r"\[(\s*\d+\s*(?:,\s*\d+\s*)*)\]")


class CitationFilter:
    """Passes streamed text through, holding back anything that could be a ``[n]`` marker
    until it closes; markers that match no evidence are dropped and counted."""

    def __init__(self, evidence: Mapping[int, Evidence]) -> None:
        self._evidence = evidence
        self._buffer = ""
        self.used: list[int] = []
        self.stripped = 0

    def feed(self, text: str) -> str:
        self._buffer += text
        out: list[str] = []
        while self._buffer:
            start = self._buffer.find("[")
            if start < 0:
                out.append(self._buffer)
                self._buffer = ""
                break
            out.append(self._buffer[:start])
            rest = self._buffer[start:]
            end = rest.find("]")
            if end < 0:
                if len(rest) > 24 or not re.fullmatch(r"\[[\d,\s]*", rest):
                    out.append(rest[0])
                    self._buffer = rest[1:]
                    continue
                self._buffer = rest
                break
            token = rest[: end + 1]
            self._buffer = rest[end + 1 :]
            out.append(self._marker(token))
        return "".join(out)

    def finish(self) -> str:
        tail, self._buffer = self._buffer, ""
        return tail

    def _marker(self, token: str) -> str:
        match = _MARKER.fullmatch(token)
        if match is None:
            return token
        numbers = [int(n) for n in re.findall(r"\d+", match.group(1))]
        good = [n for n in numbers if n in self._evidence]
        self.stripped += len(numbers) - len(good)
        for n in good:
            if n not in self.used:
                self.used.append(n)
        return "".join(f"[{n}]" for n in good)


def citations(used: Sequence[int], evidence: Mapping[int, Evidence]) -> list[Citation]:
    return [evidence[n].citation() for n in used if n in evidence]


def abstention(parts: Sequence[Part]) -> str:
    """The template reply for parts with nothing: one plain sentence each."""
    return " ".join(NO_EVIDENCE.format(topic=p.sub.topic) for p in parts if p.empty)


def chit_chat_context() -> str:
    return (
        "Chit-chat: nothing was looked up in the person's memory and nothing was saved. If "
        "they want something recalled, they can just ask."
    )


def conversation_offer(parts: Sequence[Part]) -> bool:
    """Did the answer rest on something I said (so saving it can be offered)?"""
    return any(e.kind == "turn" and e.role == "assistant" for p in parts for e in p.evidence)


def save_offer(cited: Sequence[Evidence]) -> SaveOffer | None:
    """The offer to save what I said, from the cited evidence; None when none of it was mine."""
    mine = [e for e in cited if e.kind == "turn" and e.role == "assistant" and e.said]
    if not mine:
        return None
    return SaveOffer(
        turn_ids=list(dict.fromkeys(e.turn_id for e in mine if e.turn_id is not None)),
        said=list(dict.fromkeys(e.said for e in mine)),
        offer=SAVE_OFFER,
    )


def save_message(offer: SaveOffer) -> str:
    """What a "yes" to the offer saves, in the person's voice, for ingestion to file."""
    return "Save what you suggested:\n" + "\n".join(offer.said)


def is_list_shape(shape: Shape) -> bool:
    return shape in (Shape.LIST, Shape.SET, Shape.TIME_WINDOW, Shape.SITUATIONAL)


def zone(tz: str) -> ZoneInfo:
    return ZoneInfo(tz)

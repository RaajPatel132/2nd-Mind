"""The save acknowledgement (S2.10, FR-1.3/1.4): built from the **committed** write log, not the
model's plan, so it can't claim a write that was held or blocked. Dates are in the user's
timezone; state changes, refusals and assumptions are said plainly."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from secondmind.core import PolicyDecision, TargetType, TimePrecision, TimeResolution
from secondmind.ingestion.entities import EntityPlan
from secondmind.ingestion.time import TurnNow
from secondmind.memory import (
    CommitResult,
    CreateItem,
    FulfilIntention,
    OpOutcome,
    SupersedeItem,
    UpdateItem,
    format_short,
    rrule_words,
)

_KIND_PHRASE = {
    "fact": "a fact",
    "preference": "a preference",
    "episode": "a log entry",
    "plan": "a plan",
    "task": "a task",
    "intention": "a wish",
    "resource": "a saved link",
    "note": "a note",
    "rule": "a rule",
}
_INTENT_PHRASE = {
    "watch": "Added {title} to your watch list",
    "read": "Added {title} to your reading list",
    "listen": "Added {title} to things to listen to",
    "learn": "Added {title} to things to learn",
    "buy": "Added {title} to things to buy",
    "visit": "Added {title} to places to visit",
    "try": "Added {title} to things to try",
    "gift": "Saved a gift idea: {title}",
}
_DONE = {
    "watch": "watched",
    "read": "read",
    "learn": "learned",
    "buy": "bought",
    "visit": "visited",
    "try": "tried",
    "gift": "given",
}


@dataclass(slots=True)
class AckFacts:
    commit: CommitResult
    now: TurnNow
    new_people: Sequence[EntityPlan] = ()
    assumed_times: Sequence[TimeResolution] = ()
    unresolved: Sequence[str] = ()
    lines: list[str] = field(default_factory=list)


def refusal() -> str:
    return (
        "I didn't save that: it looks like a password or another secret, and I don't store "
        "those. Nothing was written."
    )


def acknowledge(facts: AckFacts) -> str:
    lines: list[str] = []
    after = facts.commit.items_after()
    replacing = {
        o.op.new_id
        for o in facts.commit.outcomes
        if isinstance(o.op, SupersedeItem) and o.verdict.decision is PolicyDecision.ALLOWED
    }
    for outcome in facts.commit.outcomes:
        if isinstance(outcome.op, CreateItem) and outcome.op.item_id in replacing:
            continue  # said once, by the supersede line
        line = _line(outcome, after, facts)
        if line:
            lines.append(line)
    for note in facts.commit.notes:
        if note.op == "not_written":
            if note.reconcile is not None:
                lines.append(f"Already saved: {note.title}.")
            else:
                lines.append(f"Not saved: {note.title} ({note.reason}).")
    for person in facts.new_people:
        mention = person.resolution.mention
        lines.append(
            f"I've assumed '{mention}' is someone new; tell me their name and I'll update it."
        )
    for t in facts.assumed_times:
        alt = f" (not {t.alternative})" if t.alternative else ""
        lines.append(f"I took '{t.expression}' as {_value(t, facts.now)}{alt}.")
    lines.extend(
        f"I couldn't work out '{expression}', so I saved it without that date."
        for expression in facts.unresolved
    )
    if not lines:
        return "Nothing new to save there."
    return " ".join(lines)


def _line(  # noqa: PLR0911, PLR0912
    outcome: OpOutcome, after: dict[Any, dict[str, Any]], facts: AckFacts
) -> str | None:
    op, verdict = outcome.op, outcome.verdict
    tz, now = facts.now.timezone, facts.now.instant
    if verdict.decision is PolicyDecision.HELD:
        title = outcome.entries[0].title if outcome.entries else op.title
        return f"Held for your confirmation: {title} ({verdict.reason})."
    if verdict.decision is PolicyDecision.BLOCKED:
        if verdict.rule_id == "P-SECRET-1":
            return refusal()
        if verdict.rule_id == "P-MOD-1":
            return f"I didn't store '{op.title}' as a fact: it's hypothetical."
        if verdict.rule_id == "CORE-BUDGET":
            return f"{op.title} is saved, but core memory is full, so it isn't always in context."
        if verdict.rule_id == "NOT-APPLICABLE":
            return None
        return f"Not saved: {op.title} ({verdict.reason})."
    if isinstance(op, CreateItem):
        item = after.get(op.item_id)
        if item is None:
            return None
        return _saved(item, outcome, tz, now)
    if isinstance(op, SupersedeItem):
        old = next((r.before for r in outcome.rows if r.target_type is TargetType.ITEM), None)
        new = after.get(op.new_id)
        if old is None or new is None:
            return None
        was = _value_text(old) or old.get("title", "the old value")
        until = format_short(op.valid_to, TimePrecision.DAY, tz, now)
        if format_short(now, TimePrecision.DAY, tz, now) == until:
            until = "today"
        if old.get("kind") == "plan":
            was_on = _when(old, tz, now)
            return f"Moved: {new.get('title')} is now {_when(new, tz, now)} (was {was_on})."
        return f"Updated: {_second_person(str(new.get('text', '')))} now ({was} until {until})."
    if isinstance(op, FulfilIntention):
        intention = next((r.after for r in outcome.rows if r.target_type is TargetType.ITEM), None)
        if intention is None:
            return None
        done = _DONE.get(str(intention.get("subtype") or ""), "done")
        return f"Marked {_object(intention)} as {done}."
    if isinstance(op, UpdateItem):
        if op.changes.get("in_core"):
            return f"Added to core memory: {op.title}."
        if op.reconcile is not None:
            return f"Added detail to {op.title}."
    return None


def _saved(item: dict[str, Any], outcome: OpOutcome, tz: str, now: datetime) -> str:
    kind = str(item.get("kind"))
    subtype = str(item.get("subtype") or "")
    title = str(item.get("title", ""))
    if kind == "intention" and subtype in _INTENT_PHRASE:
        text = _INTENT_PHRASE[subtype].format(title=title)
    elif kind == "episode":
        text = f"Logged: {title}"
    elif item.get("rrule"):
        text = f"Saved as a repeating plan: {title}"
    else:
        text = f"Saved as {_KIND_PHRASE.get(kind, 'a memory')}: {title}"
    when = _when(item, tz, now)
    if item.get("rrule") and item.get("occurred_start"):
        start = datetime.fromisoformat(str(item["occurred_start"]))
        when = rrule_words(str(item["rrule"]), tz, start)
    if when and when not in title:
        text += f", {when}"
    text += "."
    for row in outcome.rows:
        if row.target_type is TargetType.TRIGGER and row.after and row.after.get("fires_at"):
            fires = datetime.fromisoformat(str(row.after["fires_at"]))
            text += f" I'll remind you {format_short(fires, TimePrecision.DAY, tz, now)}."
    return text


def _when(item: dict[str, Any], tz: str, now: datetime) -> str:
    precision = TimePrecision(item["time_precision"]) if item.get("time_precision") else None
    for name in ("occurred_start", "due_at", "valid_from"):
        value = item.get(name)
        if value:
            dt = datetime.fromisoformat(str(value))
            text = format_short(dt, precision, tz, now)
            return (
                f"due {text}"
                if name == "due_at"
                else (f"since {text}" if name == "valid_from" else text)
            )
    return ""


def _value(t: TimeResolution, now: TurnNow) -> str:
    tz = ZoneInfo(now.timezone)
    try:
        if t.precision is TimePrecision.YEAR:
            dt = datetime(int(t.value), 1, 1, tzinfo=tz)
        elif t.precision is TimePrecision.MONTH:
            year, month = t.value.split("-")
            dt = datetime(int(year), int(month), 1, tzinfo=tz)
        else:
            dt = datetime.fromisoformat(t.value).replace(tzinfo=tz)
    except ValueError:
        return t.value
    return format_short(dt, t.precision, now.timezone, now.instant)


def _value_text(item: dict[str, Any]) -> str | None:
    value = item.get("value") or {}
    text = value.get("text") if isinstance(value, dict) else None
    return str(text) if text else None


def _object(intention: dict[str, Any]) -> str:
    title = str(intention.get("title", "it"))
    subtype = str(intention.get("subtype") or "")
    return (
        re.sub(rf"^{re.escape(subtype)}\s+", "", title, flags=re.IGNORECASE) if subtype else title
    )


_SECOND_PERSON = (
    (re.compile(r"\bI'm\b"), "you're"),
    (re.compile(r"\bI am\b"), "you are"),
    (re.compile(r"\bI was\b"), "you were"),
    (re.compile(r"\bI\b"), "you"),
    (re.compile(r"\bmy\b", re.IGNORECASE), "your"),
    (re.compile(r"\bme\b"), "you"),
    (re.compile(r"\bmine\b"), "yours"),
    (re.compile(r"\bmyself\b"), "yourself"),
)


def _second_person(text: str) -> str:
    text = " ".join(text.split()).rstrip(".")
    for pattern, repl in _SECOND_PERSON:
        text = pattern.sub(repl, text)
    return text[:1].lower() + text[1:] if text[:3].lower() == "you" else text


def summarise_commit(commit: CommitResult, *, prefix: str) -> str:
    """A plain summary of a non-chat turn (undo, confirm, system job) from its committed diff."""
    entries = commit.diff.entries
    groups: dict[str, list[str]] = {}
    for entry in entries:
        groups.setdefault(entry.op, []).append(entry.title)
    parts: list[str] = []
    labels = (
        ("removed", "removed"),
        ("added", "restored"),
        ("updated", "changed"),
        ("superseded", "superseded"),
        ("fulfilled", "marked done"),
    )
    for op, label in labels:
        titles = groups.get(op, [])
        if titles:
            parts.append(f"{label} {_join(titles)}")
    lines = [f"{prefix}: {'; '.join(parts)}."] if parts else []
    lines.extend(f"Kept '{e.title}': {e.reason}." for e in entries if e.op == "conflict")
    lines.extend(
        f"Held for your confirmation: {e.title} ({e.reason})." for e in entries if e.op == "held"
    )
    lines.extend(f"Not changed: {e.title} ({e.reason})." for e in entries if e.op == "not_written")
    return " ".join(lines) or f"{prefix}: nothing to change."


def _join(titles: Sequence[str]) -> str:
    quoted = [f"'{t}'" for t in dict.fromkeys(titles)]
    if len(quoted) <= 1:
        return "".join(quoted)
    return ", ".join(quoted[:-1]) + " and " + quoted[-1]

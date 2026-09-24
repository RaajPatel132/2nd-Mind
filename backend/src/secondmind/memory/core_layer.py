"""Core memory (S2.9): current state only, rendered into a stable prompt prefix within a token
budget. Pure: callers load the rows, this decides what goes in and how it reads."""

import math
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from secondmind.core import EntityKind, ItemStatus, Kind, Sensitivity
from secondmind.memory.records import EntityRecord, ItemRecord

HEADER = "## Core memory (what I always know about you)"
_CORE_STATES = frozenset({"current", "active", "confirmed"})


def estimate_tokens(text: str) -> int:
    """~4 characters per token: close enough for a budget, and provider-independent."""
    return math.ceil(len(text) / 4) if text else 0


def core_line(item: ItemRecord) -> str:
    return f"- {' '.join(item.text.split())}"


def core_tokens_with(items: Iterable[ItemRecord], candidate: ItemRecord) -> int:
    lines = [HEADER, *(core_line(i) for i in items if in_core_view(i)), core_line(candidate)]
    return estimate_tokens("\n".join(lines))


def in_core_view(item: ItemRecord) -> bool:
    """Current state only; sensitive items only once the user confirmed them."""
    if not item.in_core or item.status is not ItemStatus.ACTIVE or item.valid_to is not None:
        return False
    if item.state not in _CORE_STATES:
        return False
    return item.sensitivity is not Sensitivity.SENSITIVE or item.core_confirmed_at is not None


@dataclass(frozen=True, slots=True)
class CoreView:
    text: str
    tokens: int
    item_ids: tuple[uuid.UUID, ...]
    truncated: bool = False


def render_core(
    core_items: Sequence[ItemRecord],
    *,
    entities: Mapping[uuid.UUID, EntityRecord],
    item_entities: Mapping[uuid.UUID, Sequence[uuid.UUID]],
    open_intentions: Sequence[ItemRecord],
    budget: int,
) -> CoreView:
    """Sections in a fixed order so the prefix is stable between turns (prompt caching)."""
    visible = [i for i in core_items if in_core_view(i)]
    about: list[str] = []
    people: dict[uuid.UUID, list[str]] = {}
    prefs: list[str] = []
    goals: list[str] = []
    patterns: list[str] = []
    rules: list[str] = []
    ids: list[uuid.UUID] = []
    for item in sorted(visible, key=lambda i: (i.created_at, i.id)):
        ids.append(item.id)
        line = core_line(item)
        person = _key_person(item, entities, item_entities)
        if item.kind is Kind.RULE:
            rules.append(line)
        elif item.kind is Kind.PATTERN:
            patterns.append(line)
        elif item.kind is Kind.INTENTION:
            goals.append(line)
        elif person is not None:
            people.setdefault(person, []).append(line)
        elif item.kind is Kind.PREFERENCE:
            prefs.append(line)
        else:
            about.append(line)
    goals.extend(
        f"- Project: {e.name}"
        for e in entities.values()
        if e.kind is EntityKind.PROJECT and e.status == "active"
    )

    sections: list[tuple[str, list[str]]] = [
        ("About me", about),
        (
            "Key people",
            [
                f"- {entities[pid].display}:\n  " + "\n  ".join(lines)
                for pid, lines in people.items()
            ],
        ),
        ("Preferences", prefs),
        ("Active goals", goals),
        ("Open intentions", _intentions_summary(open_intentions)),
        ("Confirmed patterns", patterns),
        ("Rules", rules),
    ]
    lines = [HEADER]
    for title, body in sections:
        lines.append(f"### {title}")
        lines.extend(body or ["- (none yet)"])
    text = "\n".join(lines)
    truncated = False
    while estimate_tokens(text) > budget and len(lines) > 1:
        lines.pop()
        truncated = True
        text = "\n".join([*lines, "- (more in the archive)"])
    return CoreView(
        text=text, tokens=estimate_tokens(text), item_ids=tuple(ids), truncated=truncated
    )


def _key_person(
    item: ItemRecord,
    entities: Mapping[uuid.UUID, EntityRecord],
    item_entities: Mapping[uuid.UUID, Sequence[uuid.UUID]],
) -> uuid.UUID | None:
    candidates = [item.subject_entity_id, *item_entities.get(item.id, ())]
    for entity_id in candidates:
        entity = entities.get(entity_id) if entity_id else None
        if entity and entity.kind is EntityKind.PERSON and entity.is_key:
            return entity.id
    return None


def _intentions_summary(open_intentions: Sequence[ItemRecord]) -> list[str]:
    live = [i for i in open_intentions if i.state in ("wanted", "active")]
    if not live:
        return []
    counts = Counter(i.subtype or "other" for i in live)
    summary = ", ".join(f"{n} to {subtype}" for subtype, n in sorted(counts.items()))
    recent = sorted(live, key=lambda i: i.created_at, reverse=True)[:3]
    return [f"- {summary}", "- Most recent: " + "; ".join(i.title for i in recent)]

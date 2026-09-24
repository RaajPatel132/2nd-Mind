"""Entity resolution (S2.7, FR-3.3). Every mention ("my wife", "Kabir", "Cubbon Park",
"Severance", "Rust") is matched against the workspace's entities by name, alias and relationship
label. Deterministic matching comes first; the model is asked to choose only when several
candidates match. No match creates an entity of the right kind; a later name for a label-only
person ("my wife Kabir") updates that entity instead of making a second one.
"""

import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from secondmind.core import EntityKind, EntityResolution, new_id
from secondmind.ingestion.schemas import ExtractedEntity
from secondmind.memory import EntityContent, EntityRecord, UpsertEntity

SELF_WORDS = frozenset({"i", "me", "my", "myself", "mine", "self", "user", "the user"})

# People with these labels are key people: facts about them are proposed for core memory.
CLOSE_LABELS = frozenset(
    {
        "wife", "husband", "partner", "spouse", "boyfriend", "girlfriend", "fiance", "fiancee",
        "mother", "mom", "mum", "father", "dad", "sister", "brother", "son", "daughter",
        "grandmother", "grandfather", "grandma", "grandpa", "best friend",
    }
)  # fmt: skip

# Chooses among candidates when matching is ambiguous: returns a candidate index or None.
Chooser = Callable[[ExtractedEntity, Sequence[EntityRecord]], Awaitable[tuple[int | None, str]]]


@dataclass(frozen=True, slots=True)
class EntityPlan:
    """What one extracted mention became."""

    ref: str
    entity_id: uuid.UUID
    kind: EntityKind
    name: str
    op: UpsertEntity | None
    resolution: EntityResolution
    is_key: bool = False
    assumed_new_person: bool = False


def _clean(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"^(my|our|the|a|an)\s+", "", text)
    text = re.sub("['\u2019]s$", "", text)
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _keys(entity: EntityRecord) -> set[str]:
    return {_clean(entity.name), *(_clean(a) for a in entity.aliases)} - {""}


def is_key_label(label: str | None) -> bool:
    return bool(label) and _clean(label or "") in CLOSE_LABELS


async def resolve_entities(
    extracted: Sequence[ExtractedEntity],
    known: Sequence[EntityRecord],
    me: EntityRecord,
    *,
    now: datetime,
    choose: Chooser | None = None,
) -> dict[str, EntityPlan]:
    plans: dict[str, EntityPlan] = {
        "self": EntityPlan(
            ref="self",
            entity_id=me.id,
            kind=EntityKind.SELF,
            name="me",
            op=None,
            resolution=_resolution("I / me", me, "matched", "the user"),
        )
    }
    pool = [e for e in known if e.kind is not EntityKind.SELF and e.status == "active"]
    for x in extracted:
        if x.kind == "self" or _clean(x.mention) in SELF_WORDS or x.mention.lower() in SELF_WORDS:
            plans[x.id] = EntityPlan(
                ref=x.id,
                entity_id=me.id,
                kind=EntityKind.SELF,
                name="me",
                op=None,
                resolution=_resolution(x.mention, me, "matched", "first person: the user"),
            )
            continue
        plans[x.id] = await _resolve_one(x, pool, choose)
        created = plans[x.id].op
        if created is not None and created.create:
            # A second mention of the same new thing in this message reuses it.
            pool.append(_as_record(created, me.workspace_id, now))
    return plans


async def _resolve_one(
    x: ExtractedEntity, pool: Sequence[EntityRecord], choose: Chooser | None
) -> EntityPlan:
    kind = EntityKind(x.kind)
    name_key = _clean(x.name or "")
    mention_key = _clean(x.mention)
    label_key = _clean(x.label or "")
    by_name = [e for e in pool if name_key and name_key in _keys(e)]
    by_mention = [e for e in pool if mention_key and mention_key in _keys(e)]
    by_label = [
        e
        for e in pool
        if label_key
        and e.kind is EntityKind.PERSON
        and label_key in {_clean(lb) for lb in e.labels}
    ]
    candidates = _dedupe(by_name or by_mention or by_label)
    same_kind = [e for e in candidates if e.kind is kind]
    if len(same_kind) >= 1:
        candidates = same_kind
    if not candidates:
        return _new(x, kind)
    outcome = "matched"
    chosen = candidates[0]
    rationale = _why(x, chosen, by_name, by_mention)
    alternatives: list[str] = []
    if len(candidates) > 1:
        outcome = "ambiguous"
        alternatives = [c.display for c in candidates]
        index, reason = (None, "no chooser") if choose is None else await choose(x, candidates)
        if index is None and choose is not None:
            return _new(x, kind, note=f"none of {len(candidates)} candidates fitted: {reason}")
        chosen = candidates[index or 0]
        rationale = f"{len(candidates)} candidates; picked {chosen.display}: {reason}"
    update = _update_for(x, chosen)
    if update is not None:
        outcome = "updated" if outcome == "matched" else outcome
        rationale += f"; {update.rationale}"
    resolution = EntityResolution(
        mention=x.mention,
        entity_id=chosen.id,
        entity_kind=chosen.kind,
        display_name=update.entity.name if update else chosen.name,
        outcome=outcome,  # type: ignore[arg-type]
        created=False,
        candidates=[a for a in alternatives if a != chosen.display],
        rationale=rationale,
    )
    return EntityPlan(
        ref=x.id,
        entity_id=chosen.id,
        kind=chosen.kind,
        name=update.entity.name if update else chosen.name,
        op=update,
        resolution=resolution,
        is_key=(update.entity.is_key if update else chosen.is_key),
    )


def _why(
    x: ExtractedEntity,
    chosen: EntityRecord,
    by_name: Sequence[EntityRecord],
    by_mention: Sequence[EntityRecord],
) -> str:
    if chosen in by_name:
        return f"'{x.mention}' → {chosen.display} (name match)"
    if chosen in by_mention:
        return f"'{x.mention}' → {chosen.display} (alias match)"
    return f"'{x.mention}' → {chosen.display} (relationship label match)"


def _update_for(x: ExtractedEntity, entity: EntityRecord) -> UpsertEntity | None:
    """New facts about a known entity: a real name for a label-only person, a new label."""
    name, aliases, labels = entity.name, list(entity.aliases), list(entity.labels)
    changes: list[str] = []
    placeholder = entity.attributes.get("name_known") is False
    if x.name and placeholder and _clean(x.name) != _clean(entity.name):
        aliases.append(entity.name)
        name = x.name.strip()
        changes.append(f"named {name}")
    if x.label and _clean(x.label) not in {_clean(lb) for lb in labels}:
        labels.append(x.label.strip().lower())
        changes.append(f"label {x.label.strip().lower()}")
    if (
        x.mention.strip()
        and _clean(x.mention) not in _keys(entity)
        and _clean(x.mention) not in {_clean(lb) for lb in labels}
    ):
        aliases.append(x.mention.strip())
    if not changes:
        return None
    attributes = dict(entity.attributes)
    if name != entity.name:
        attributes["name_known"] = True
    content = EntityContent(
        kind=entity.kind,
        name=name,
        aliases=_unique(aliases),
        labels=_unique(labels),
        is_key=entity.is_key or any(is_key_label(lb) for lb in labels),
        attributes=attributes,
        summary=entity.summary,
        summary_updated_at=entity.summary_updated_at,
    )
    return UpsertEntity(
        entity_id=entity.id,
        entity=content,
        create=False,
        title=name,
        rationale=", ".join(changes),
    )


def _new(x: ExtractedEntity, kind: EntityKind, note: str = "no match") -> EntityPlan:
    label = (x.label or "").strip().lower() or None
    name = (x.name or "").strip() or label or _display(x.mention)
    aliases = [x.mention.strip()] if _clean(x.mention) not in {_clean(name), ""} else []
    key = kind is EntityKind.PERSON and is_key_label(label)
    attributes: dict[str, object] = {}
    if kind is EntityKind.PERSON:
        attributes["name_known"] = bool(x.name)
    entity_id = new_id()
    op = UpsertEntity(
        entity_id=entity_id,
        entity=EntityContent(
            kind=kind,
            name=name,
            aliases=aliases,
            labels=[label] if label else [],
            is_key=key,
            attributes=attributes,
        ),
        create=True,
        title=name,
        rationale=f"'{x.mention}' → {note} → new {kind.value} {name}",
    )
    resolution = EntityResolution(
        mention=x.mention,
        entity_id=entity_id,
        entity_kind=kind,
        display_name=name,
        outcome="new",
        created=True,
        rationale=f"'{x.mention}' → {note} → new {kind.value} *{name}*",
    )
    return EntityPlan(
        ref=x.id,
        entity_id=entity_id,
        kind=kind,
        name=name,
        op=op,
        resolution=resolution,
        is_key=key,
        assumed_new_person=kind is EntityKind.PERSON and not x.name,
    )


def _display(mention: str) -> str:
    cleaned = re.sub(r"^(my|our|the|a|an)\s+", "", mention.strip(), flags=re.IGNORECASE)
    return cleaned or mention.strip()


def _resolution(mention: str, entity: EntityRecord, outcome: str, why: str) -> EntityResolution:
    return EntityResolution(
        mention=mention,
        entity_id=entity.id,
        entity_kind=entity.kind,
        display_name=entity.name,
        outcome=outcome,  # type: ignore[arg-type]
        created=False,
        rationale=why,
    )


def _as_record(op: UpsertEntity, workspace_id: uuid.UUID, now: datetime) -> EntityRecord:
    return EntityRecord(
        **op.entity.model_dump(),
        id=op.entity_id,
        workspace_id=workspace_id,
        created_at=now,
        updated_at=now,
        created_by_turn_id=None,
        updated_by_turn_id=None,
    )


def _dedupe(entities: Sequence[EntityRecord]) -> list[EntityRecord]:
    seen: set[uuid.UUID] = set()
    out = []
    for e in sorted(entities, key=lambda e: e.updated_at, reverse=True):
        if e.id not in seen:
            seen.add(e.id)
            out.append(e)
    return out


def _unique(values: Sequence[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        if v and _clean(v) not in {_clean(o) for o in out}:
            out.append(v)
    return out

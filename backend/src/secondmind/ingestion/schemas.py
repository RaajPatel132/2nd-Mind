"""Structured outputs of the ingestion steps (intent, extract, enrich, resolve, reconcile).

Every field is required and optional ones are nullable (no defaults), so the same schema works
with both providers' strict structured-output modes. Code validates the meaning on top
(``validate_extraction``); on errors extraction is retried once with the errors included.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from secondmind.core import KIND_STATES, Kind

IntentLabel = Literal["save", "recall", "save_and_recall", "correct", "chit_chat"]
KindLabel = Literal[
    "fact", "preference", "episode", "plan", "task", "intention", "resource", "note", "rule"
]
EntityKindLabel = Literal[
    "self", "person", "place", "org", "thing", "work", "topic", "project", "list"
]
RoleLabel = Literal["about", "with", "for", "by", "at", "owner", "part_of"]
ClockLabel = Literal["occurred", "valid", "due", "trigger"]
DirectionLabel = Literal["past", "future"]
LinkLabel = Literal[
    "part_of", "follows", "because", "evidence_for", "gift_for_event", "derived_from"
]
ModalityLabel = Literal["asserted", "planned", "hypothetical", "reported"]
SensitivityLabel = Literal["normal", "personal", "sensitive", "secret"]
LayerLabel = Literal["core", "quick", "archive"]
FormatLabel = Literal["article", "video", "pdf", "image", "link", "other"]


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntentOutput(_Out):
    intent: IntentLabel
    confidence: float
    reason: str


class ExtractedEntity(_Out):
    id: str
    mention: str
    name: str | None
    kind: EntityKindLabel
    label: str | None


class TimeExpression(_Out):
    id: str
    expression: str
    clock: ClockLabel
    direction: DirectionLabel | None
    recurring: bool


class EntityRef(_Out):
    entity: str
    role: RoleLabel


class Attribute(_Out):
    key: str
    value: str


class StatementValue(_Out):
    text: str | None
    number: float | None
    unit: str | None


class Rating(_Out):
    value: float
    scale: float


class MemoryLinkOut(_Out):
    type: LinkLabel
    to: str


class Reminder(_Out):
    lead: str | None


class MomentTrigger(_Out):
    """A reminder for a moment rather than a time (FR-6.9): a person comes up, or a topic or
    situation does."""

    on: Literal["person", "topic", "situation"]
    entity: str | None
    cue: str | None


class ProposedMemory(_Out):
    ref: str
    kind: KindLabel
    subtype: str | None
    format: FormatLabel | None
    state: str | None
    title: str
    text: str
    summary: str | None
    category: str | None
    tags: list[str]
    attributes: list[Attribute]
    subject: str | None
    predicate: str | None
    value: StatementValue | None
    entities: list[EntityRef]
    times: list[TimeExpression]
    reminder: Reminder | None
    trigger: MomentTrigger | None
    modality: ModalityLabel
    sentiment: int | None
    rating: Rating | None
    sensitivity: SensitivityLabel
    importance: int
    links: list[MemoryLinkOut]
    layer: LayerLabel
    layer_rationale: str
    rationale: str


class Relation(_Out):
    src: str
    relation: str
    dst: str
    evidence: str | None


class NotWritten(_Out):
    what: str
    reason: str


class ExtractOutput(_Out):
    entities: list[ExtractedEntity]
    memories: list[ProposedMemory]
    relations: list[Relation]
    not_written: list[NotWritten]
    secret_spans: list[str]


class EnrichedMemory(_Out):
    ref: str
    alt: list[str]
    cues: list[str]


class EnrichOutput(_Out):
    memories: list[EnrichedMemory]


class EntityChoice(_Out):
    candidate: str | None
    reason: str


class ReconcileChoice(_Out):
    decision: Literal["new", "add_detail", "supersede", "fulfil", "link", "no_op"]
    candidate: str | None
    reason: str


_PLACEHOLDER = re.compile(r"\[\[(t\d+)\]\]")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_\- /]*$")


def validate_extraction(out: ExtractOutput) -> list[str]:
    """Meaning-level checks the JSON schema can't express. Empty list = valid."""
    errors: list[str] = []
    entity_ids = {e.id for e in out.entities}
    if len(entity_ids) != len(out.entities):
        errors.append("entity ids must be unique")
    refs = [m.ref for m in out.memories]
    if len(set(refs)) != len(refs):
        errors.append("memory refs must be unique")
    known = entity_ids | {"self"}
    for m in out.memories:
        where = f"memory {m.ref}"
        if m.state is not None and m.state not in KIND_STATES[Kind(m.kind)]:
            errors.append(
                f"{where}: state {m.state!r} is not one of {list(KIND_STATES[Kind(m.kind)])}"
            )
        if m.format is not None and m.kind != "resource":
            errors.append(f"{where}: format is only for kind resource")
        if m.subject is not None and m.subject not in known:
            errors.append(f"{where}: subject {m.subject!r} is not an entity id or 'self'")
        errors.extend(
            f"{where}: entity {ref.entity!r} is not an entity id or 'self'"
            for ref in m.entities
            if ref.entity not in known
        )
        time_ids = {t.id for t in m.times}
        for text in (m.text, m.title):
            errors.extend(
                f"{where}: placeholder [[{tid}]] has no time expression with that id"
                for tid in _PLACEHOLDER.findall(text)
                if tid not in time_ids
            )
        errors.extend(
            f"{where}: link to unknown memory {link.to!r}"
            for link in m.links
            if link.to not in refs
        )
        if not 1 <= m.importance <= 5:
            errors.append(f"{where}: importance must be 1-5")
        if m.sentiment is not None and not -2 <= m.sentiment <= 2:
            errors.append(f"{where}: sentiment must be -2..2")
        for slug_field in ("subtype", "predicate"):
            value = getattr(m, slug_field)
            if value is not None and not _SLUG.match(value.lower()):
                errors.append(f"{where}: {slug_field} {value!r} is not a slug")
        if not m.text.strip() or not m.title.strip():
            errors.append(f"{where}: title and text are required")
    for rel in out.relations:
        if rel.src not in known or rel.dst not in known:
            errors.append(f"relation {rel.relation!r}: src and dst must be entity ids or 'self'")
        if rel.evidence is not None and rel.evidence not in refs:
            errors.append(f"relation {rel.relation!r}: evidence {rel.evidence!r} is not a memory")
    return errors

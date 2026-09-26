"""The recall fixture (S3.1): ``evals/fixtures/recall.yaml`` as a workspace.

``seed_workspace`` writes the fixture's entities, items, links and relations through
``MemoryWriter`` as **one system turn** (no extract step, so it's deterministic), renders their
keys exactly as ingestion does (same indexer, same embedder), and stores the past turns and
indexes what was said. Tests load it into a fresh workspace per module; ``make seed-dev``
loads it into the dev workspace; the E2E suite loads it through a dev-only endpoint.
"""

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field

from secondmind.agent import TraceStatus, TurnKind, TurnOutcome, TurnStatus, TurnStore
from secondmind.config import DEFAULT_RESOURCES_DIR
from secondmind.core import (
    EntityKind,
    EntityRole,
    KeyKind,
    Kind,
    LinkType,
    ResourceFormat,
    Sensitivity,
    TimePrecision,
    TriggerOn,
    UsageTotals,
    VocabKind,
    WorkspaceScope,
    initial_state,
    new_id,
)
from secondmind.memory import (
    CreateItem,
    Embedder,
    EntityContent,
    EntityLink,
    FulfilIntention,
    ItemContent,
    LinkItems,
    Memory,
    NewTrigger,
    Op,
    RelateEntities,
    SupersedeItem,
    TriggerContent,
    UpsertEntity,
    WriterTurn,
    content_hash,
    quick_layer,
)
from secondmind.retrieval import ConversationIndexer, ConversationStore, SaidTurn

# Relative to the resources directory (RESOURCES_DIR): an installed package is not next to it.
FIXTURE_FILE = Path("evals") / "fixtures" / "recall.yaml"
FIXTURE_PATH = DEFAULT_RESOURCES_DIR / FIXTURE_FILE
SYSTEM_TEXT = "Load the recall fixture"


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EntitySpec(_Spec):
    key: str
    kind: EntityKind
    name: str
    aliases: list[str] = []
    labels: list[str] = []
    is_key: bool = False
    attributes: dict[str, str] = {}


class RoleSpec(_Spec):
    entity: str
    role: EntityRole


class TimeSpec(_Spec):
    at: str
    precision: TimePrecision = TimePrecision.DAY


class OccurredSpec(_Spec):
    start: str
    end: str | None = None
    precision: TimePrecision = TimePrecision.DAY


class TriggerSpec(_Spec):
    on: TriggerOn
    entity: str | None = None
    cue: str | None = None
    fires_at: str | None = None
    lead_minutes: int | None = None


class ItemSpec(_Spec):
    key: str
    kind: Kind
    subtype: str | None = None
    format: ResourceFormat | None = None
    state: str | None = None
    title: str
    text: str
    summary: str | None = None
    subject: str | None = None
    predicate: str | None = None
    value: str | dict[str, Any] | None = None
    attributes: dict[str, str] = {}
    entities: list[RoleSpec] = []
    category: str | None = None
    tags: list[str] = []
    occurred: OccurredSpec | None = None
    rrule: str | None = None
    due: TimeSpec | None = None
    valid_from: TimeSpec | None = None
    mentioned: str
    sensitivity: Sensitivity = Sensitivity.NORMAL
    sentiment: int | None = None
    in_core: bool = False
    triggers: list[TriggerSpec] = []


class LinkSpec(_Spec):
    type: LinkType
    src: str
    dst: str
    valid_to: str | None = None


class RelationSpec(_Spec):
    src: str
    relation: str
    dst: str
    evidence: str | None = None


class TurnSpec(_Spec):
    key: str
    at: str
    user: str
    assistant: str


class WorkspaceSpec(_Spec):
    entities: list[EntitySpec] = []
    relations: list[RelationSpec] = []
    items: list[ItemSpec] = []
    links: list[LinkSpec] = []
    turns: list[TurnSpec] = []


class RecallFixture(WorkspaceSpec):
    now: str
    timezone: str
    other: WorkspaceSpec = Field(default_factory=WorkspaceSpec)

    @property
    def instant(self) -> datetime:
        return local_instant(self.now, self.timezone)


def load_fixture(path: Path = FIXTURE_PATH) -> RecallFixture:
    return RecallFixture.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def local_instant(text: str, tz: str) -> datetime:
    """ "2026-09-02T06:30" (local) or "2026-09-02" or "...Z" (UTC) -> a UTC instant."""
    if text.endswith("Z"):
        return datetime.fromisoformat(text.removesuffix("Z")).replace(tzinfo=UTC)
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(tz)).astimezone(UTC)


@dataclass(slots=True)
class Seeded:
    """Ids of what a seed wrote, by fixture key."""

    scope: WorkspaceScope
    system_turn: uuid.UUID
    items: dict[str, uuid.UUID] = field(default_factory=dict)
    entities: dict[str, uuid.UUID] = field(default_factory=dict)
    turns: dict[str, uuid.UUID] = field(default_factory=dict)

    def key_of(self, item_id: uuid.UUID) -> str | None:
        return next((k for k, v in self.items.items() if v == item_id), None)


IndexStore = Callable[[WorkspaceScope], ConversationStore]


async def seed_workspace(
    spec: WorkspaceSpec,
    *,
    memory: Memory,
    turns: TurnStore,
    scope: WorkspaceScope,
    timezone: str,
    now: datetime,
    embed: Embedder | None,
    embedding_model: str,
    conversation: ConversationStore | None = None,
    config_hash: str = "0" * 64,
) -> Seeded:
    """Write ``spec`` into ``scope``'s (empty) workspace. See the module docstring."""
    past = [(t, local_instant(t.at, timezone)) for t in spec.turns]
    turn_ids: dict[str, uuid.UUID] = {}
    indexer = (
        ConversationIndexer(conversation, embed=embed, model=embedding_model)
        if conversation is not None
        else None
    )
    for t, at in sorted(past, key=lambda p: p[1]):
        stored = await turns.create(
            turn_id=new_id(), text=t.user, config_hash=config_hash, started_at=at
        )
        done = await turns.finish(stored.id, _outcome(t.assistant, at))
        turn_ids[t.key] = done.id
        if indexer is not None:
            await indexer.index(
                SaidTurn(
                    id=done.id,
                    input=done.input,
                    output=done.output,
                    started_at=done.started_at,
                    completed=True,
                    chat=True,
                )
            )

    system = await turns.create(
        turn_id=new_id(),
        text=SYSTEM_TEXT,
        config_hash=config_hash,
        started_at=now,
        kind=TurnKind.SYSTEM,
    )
    me = await memory.reader(scope).self_entity()
    entity_ids = {"self": me.id} | {e.key: new_id() for e in spec.entities}
    item_ids = {i.key: new_id() for i in spec.items}
    ops, cues = _ops(spec, entity_ids, item_ids, timezone=timezone, now=now)
    writer = memory.writer(
        scope,
        WriterTurn(turn_id=system.id, workspace_id=scope.workspace_id, kind="system", now=now),
        confirmed=True,
    )
    writer.add(*ops)
    for item in spec.items:
        if item.subtype:
            writer.register_vocab(VocabKind.SUBTYPE, item.subtype)
        if item.predicate:
            writer.register_vocab(VocabKind.PREDICATE, item.predicate)
    for rel in spec.relations:
        writer.register_vocab(VocabKind.RELATION, rel.relation)
    result = await writer.commit()
    refused = [o for o in result.outcomes if o.verdict.decision.value != "allowed"]
    if refused:
        raise RuntimeError(f"the fixture write was refused: {refused[0].verdict}")
    indexer_keys = memory.keys(scope, timezone=timezone, embed=embed, model=embedding_model)
    await indexer_keys.rebuild(sorted(item_ids.values()), extra=cues)
    summary = f"Loaded the recall fixture: {len(item_ids)} memories, {len(spec.entities)} entities."
    await turns.finish(system.id, _outcome(summary, now))
    return Seeded(
        scope=scope,
        system_turn=system.id,
        items=item_ids,
        entities=entity_ids,
        turns=turn_ids,
    )


def _outcome(output: str, at: datetime) -> TurnOutcome:
    return TurnOutcome(
        status=TurnStatus.COMPLETED,
        output=output,
        usage=UsageTotals(),
        models={},
        prompt_versions=[],
        trace_status=TraceStatus.DISABLED,
        finished_at=at + timedelta(seconds=2),
    )


def _ops(
    spec: WorkspaceSpec,
    entity_ids: Mapping[str, uuid.UUID],
    item_ids: Mapping[str, uuid.UUID],
    *,
    timezone: str,
    now: datetime,
) -> tuple[list[Op], dict[uuid.UUID, list[tuple[KeyKind, str]]]]:
    ops: list[Op] = [
        UpsertEntity(
            entity_id=entity_ids[e.key],
            entity=EntityContent(
                kind=e.kind,
                name=e.name,
                aliases=e.aliases,
                labels=e.labels,
                is_key=e.is_key,
                attributes=_entity_attributes(e),
            ),
            create=True,
            title=e.name,
            origin="system",
        )
        for e in spec.entities
    ]
    # Rows a link changes start in the kind's first state; the link moves them on.
    relinked = {lk.dst for lk in spec.links if lk.type in (LinkType.SUPERSEDES, LinkType.FULFILS)}
    cues: dict[uuid.UUID, list[tuple[KeyKind, str]]] = {}
    for item in spec.items:
        content, triggers = _content(item, entity_ids, timezone=timezone, now=now)
        if item.key in relinked:
            content = content.model_copy(update={"state": initial_state(item.kind)})
        ops.append(
            CreateItem(
                item_id=item_ids[item.key],
                item=content,
                entities=[
                    EntityLink(row_id=new_id(), entity_id=entity_ids[r.entity], role=r.role)
                    for r in item.entities
                ],
                triggers=triggers,
                category_slug=item.category,
                title=item.title,
                rationale="recall fixture",
                origin="system",
            )
        )
        cues[item_ids[item.key]] = [(KeyKind.CUE, t.cue) for t in item.triggers if t.cue]
    for lk in spec.links:
        src, dst = item_ids[lk.src], item_ids[lk.dst]
        if lk.type is LinkType.SUPERSEDES:
            kind = next(i.kind for i in spec.items if i.key == lk.dst)
            ops.append(
                SupersedeItem(
                    old_id=dst,
                    new_id=src,
                    link_id=new_id(),
                    valid_to=local_instant(lk.valid_to or "", timezone),
                    state="moved" if kind is Kind.PLAN else "superseded",
                    title=lk.dst,
                    origin="system",
                )
            )
        elif lk.type is LinkType.FULFILS:
            ops.append(
                FulfilIntention(
                    intention_id=dst,
                    episode_id=src,
                    link_id=new_id(),
                    title=lk.dst,
                    origin="system",
                )
            )
        else:
            ops.append(
                LinkItems(
                    link_id=new_id(),
                    src_id=src,
                    link_type=lk.type,
                    dst_id=dst,
                    title=f"{lk.src} {lk.type.value} {lk.dst}",
                    origin="system",
                )
            )
    ops.extend(
        RelateEntities(
            relation_id=new_id(),
            src_entity_id=entity_ids[rel.src],
            relation=rel.relation,
            dst_entity_id=entity_ids[rel.dst],
            evidence_item_id=item_ids[rel.evidence] if rel.evidence else None,
            title=f"{rel.src} {rel.relation} {rel.dst}",
            origin="system",
        )
        for rel in spec.relations
    )
    return ops, {k: v for k, v in cues.items() if v}


def _entity_attributes(e: EntitySpec) -> dict[str, Any]:
    out: dict[str, Any] = dict(e.attributes)
    if e.kind is EntityKind.PERSON:
        out["name_known"] = True
    return out


def _content(
    item: ItemSpec,
    entity_ids: Mapping[str, uuid.UUID],
    *,
    timezone: str,
    now: datetime,
) -> tuple[ItemContent, list[NewTrigger]]:
    fields: dict[str, Any] = {}
    if item.occurred is not None:
        fields["occurred_start"] = local_instant(item.occurred.start, timezone)
        if item.occurred.end:
            fields["occurred_end"] = local_instant(item.occurred.end, timezone)
        fields["time_precision"] = item.occurred.precision
    if item.due is not None:
        fields["due_at"] = local_instant(item.due.at, timezone)
        fields.setdefault("time_precision", item.due.precision)
    if item.valid_from is not None:
        fields["valid_from"] = local_instant(item.valid_from.at, timezone)
        fields.setdefault("time_precision", item.valid_from.precision)
    value: dict[str, Any] | None = None
    if isinstance(item.value, str):
        value = {"text": item.value, "number": None, "unit": None}
    elif isinstance(item.value, dict):
        value = {"text": None, "number": None, "unit": None, **item.value}
    content = ItemContent(
        kind=item.kind,
        subtype=item.subtype,
        format=item.format,
        state=item.state or initial_state(item.kind),
        text=item.text,
        title=item.title,
        summary=item.summary,
        tags=item.tags,
        attributes=dict(item.attributes),
        subject_entity_id=entity_ids[item.subject] if item.subject else None,
        predicate=item.predicate,
        value=value,
        mentioned_at=local_instant(item.mentioned, timezone),
        rrule=item.rrule,
        sentiment=item.sentiment,
        sensitivity=item.sensitivity,
        in_core=item.in_core,
        raw_content=item.text,
        **fields,
    )
    triggers: list[NewTrigger] = []
    for t in item.triggers:
        spec: dict[str, Any] = {"rule": "fixture"}
        fires_at = None
        if t.on is TriggerOn.PERSON and t.entity:
            spec["entity_id"] = str(entity_ids[t.entity])
        if t.cue:
            spec |= {"cue": t.cue, "cue_hash": content_hash(t.cue)}
        if t.fires_at:
            fires_at = local_instant(t.fires_at, timezone)
            spec["lead_minutes"] = t.lead_minutes
        triggers.append(
            NewTrigger(
                trigger_id=new_id(),
                trigger=TriggerContent(on=t.on, spec=spec, fires_at=fires_at),
            )
        )
    quick = quick_layer(
        content,
        now=now,
        triggers=[t.trigger for t in triggers],
        horizon_days=30,
        recent_days=7,
    )
    content = content.model_copy(
        update={
            "in_quick": quick.in_quick,
            "quick_reason": quick.reason,
            "quick_until": quick.until,
        }
    )
    return content, triggers

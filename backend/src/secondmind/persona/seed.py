"""Writing a :class:`WorkspaceSpec` into a workspace (S3.1, S4.10).

``seed_workspace`` writes the spec's entities, items, links and relations through ``MemoryWriter``
as **one system turn** (no extract step, so it's deterministic), renders their keys exactly as
ingestion does (same indexer, same embedder), stores the past turns and indexes what was said. Saved
links and videos also get their ``link_sources`` row and their page's passages as chunk keys, so
they look as if they had been saved through the app.

Used for the recall fixture (``make seed-dev``, tests, the E2E seed) and the persona template.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from secondmind.agent import TraceStatus, TurnKind, TurnOutcome, TurnStatus, TurnStore
from secondmind.core import (
    EntityKind,
    KeyKind,
    Kind,
    LinkType,
    ResourceFormat,
    Source,
    TriggerOn,
    Trust,
    UsageTotals,
    VocabKind,
    WorkspaceScope,
    initial_state,
    new_id,
)
from secondmind.links import (
    ChunkRow,
    FetchStatus,
    LinkKind,
    LinkSource,
    canonical_url,
    site_name,
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
from secondmind.persona.spec import EntitySpec, ItemSpec, SourceSpec, WorkspaceSpec
from secondmind.persona.timing import resolve_time
from secondmind.retrieval import ConversationIndexer, ConversationStore, SaidTurn


class SourceSink(Protocol):
    """Where a seeded link's source row and its page passages go (``SqlLinkStore`` is the real one:
    the persona module never touches the links table itself)."""

    async def add_source(self, source: LinkSource, chunks: Sequence[ChunkRow]) -> None: ...


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
    sources: SourceSink | None = None,
    config_hash: str = "0" * 64,
    label: str = "the seed",
) -> Seeded:
    """Write ``spec`` into ``scope``'s (empty) workspace. See the module docstring."""
    spec = spec.expanded()
    past = [(t, resolve_time(t.at, timezone, now)) for t in spec.turns]
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
        text=f"Load {label}",
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
        raise RuntimeError(f"the seed write was refused: {refused[0].verdict}")
    indexer_keys = memory.keys(scope, timezone=timezone, embed=embed, model=embedding_model)
    await indexer_keys.rebuild(sorted(item_ids.values()), extra=cues)
    if sources is not None:
        await _write_sources(
            spec,
            item_ids,
            sources,
            scope=scope,
            turn_id=system.id,
            timezone=timezone,
            now=now,
            embed=embed,
            embedding_model=embedding_model,
        )
    summary = f"Loaded {label}: {len(item_ids)} memories, {len(spec.entities)} entities."
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


async def _write_sources(
    spec: WorkspaceSpec,
    item_ids: Mapping[str, uuid.UUID],
    sink: SourceSink,
    *,
    scope: WorkspaceScope,
    turn_id: uuid.UUID,
    timezone: str,
    now: datetime,
    embed: Embedder | None,
    embedding_model: str,
) -> None:
    for item in spec.items:
        src = item.source
        if src is None or src.kind not in ("link", "video"):
            continue
        passages = list(src.passages)
        vectors: list[list[float]] | None = None
        if passages and embed is not None:
            vectors = await embed([f"{item.title}\n\n{p}" for p in passages], 0)
        chunks = [
            ChunkRow(
                id=new_id(),
                position=i,
                text=text,
                content_hash=content_hash(text),
                embedding=None if vectors is None else vectors[i],
                embedding_model=None if vectors is None else embedding_model,
            )
            for i, text in enumerate(passages)
        ]
        await sink.add_source(
            _link_source(item, src, item_ids[item.key], scope, turn_id, len(chunks), timezone, now),
            chunks,
        )


def _link_source(  # noqa: PLR0917 - a row's worth of facts
    item: ItemSpec,
    src: SourceSpec,
    item_id: uuid.UUID,
    scope: WorkspaceScope,
    turn_id: uuid.UUID,
    chunk_count: int,
    timezone: str,
    now: datetime,
) -> LinkSource:
    url = src.url or ""
    video = src.kind == "video"
    return LinkSource(
        id=new_id(),
        workspace_id=scope.workspace_id,
        item_id=item_id,
        turn_id=turn_id,
        url=url,
        canonical_url=canonical_url(url),
        kind=LinkKind.VIDEO if video else LinkKind.LINK,
        fetch_status=FetchStatus.FULL if src.status == "full" else FetchStatus.PARTIAL,
        fetch_reason=src.reason,
        site=src.site or site_name(url),
        author=src.author,
        published_at=resolve_time(src.published, timezone, now) if src.published else None,
        word_count=src.words,
        chunk_count=chunk_count,
        final_host=site_name(url),
        status_code=200,
        content_type="text/html",
        extraction_method="oembed" if video else "article",
        channel=src.channel,
        duration_s=src.duration_s,
        fetched_at=now,
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
                rationale="seed",
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
                    valid_to=resolve_time(lk.valid_to or "", timezone, now),
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
    if item.importance is not None:
        fields["importance"] = item.importance
    if item.occurred is not None:
        fields["occurred_start"] = resolve_time(item.occurred.start, timezone, now)
        if item.occurred.end:
            fields["occurred_end"] = resolve_time(item.occurred.end, timezone, now)
        fields["time_precision"] = item.occurred.precision
    if item.due is not None:
        fields["due_at"] = resolve_time(item.due.at, timezone, now)
        fields.setdefault("time_precision", item.due.precision)
    if item.valid_from is not None:
        fields["valid_from"] = resolve_time(item.valid_from.at, timezone, now)
        fields.setdefault("time_precision", item.valid_from.precision)
    value: dict[str, Any] | None = None
    if isinstance(item.value, str):
        value = {"text": item.value, "number": None, "unit": None}
    elif isinstance(item.value, dict):
        value = {"text": None, "number": None, "unit": None, **item.value}
    origin = _origin(item)
    content = ItemContent(
        kind=item.kind,
        subtype=item.subtype,
        format=item.format or origin.get("format"),
        state=item.state or initial_state(item.kind),
        text=item.text,
        title=item.title,
        summary=item.summary,
        tags=item.tags,
        attributes=dict(item.attributes),
        subject_entity_id=entity_ids[item.subject] if item.subject else None,
        predicate=item.predicate,
        value=value,
        mentioned_at=resolve_time(item.mentioned, timezone, now),
        rrule=item.rrule,
        sentiment=item.sentiment,
        sensitivity=item.sensitivity,
        in_core=item.in_core,
        raw_content=origin.get("raw_content", item.text),
        source=origin.get("source", Source.USER_MESSAGE),
        trust=origin.get("trust", Trust.USER_STATED),
        content_ref=origin.get("content_ref"),
        **fields,
    )
    triggers: list[NewTrigger] = []
    for t in item.triggers:
        spec: dict[str, Any] = {"rule": "seed"}
        fires_at = None
        if t.on is TriggerOn.PERSON and t.entity:
            spec["entity_id"] = str(entity_ids[t.entity])
        if t.cue:
            spec |= {"cue": t.cue, "cue_hash": content_hash(t.cue)}
        if t.fires_at:
            fires_at = resolve_time(t.fires_at, timezone, now)
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
        timezone=timezone,
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


def _origin(item: ItemSpec) -> dict[str, Any]:
    """How a saved input looks in the item: a link or video is the address the person pasted; an
    image or a PDF is stored already extracted, content derived, with no original file until S9."""
    src = item.source
    if src is None:
        return {}
    if src.kind in ("link", "video"):
        return {
            "source": Source.LINK,
            "content_ref": src.url,
            "format": ResourceFormat.VIDEO if src.kind == "video" else ResourceFormat.LINK,
        }
    return {
        "source": Source.FILE,
        "trust": Trust.CONTENT_DERIVED,
        "raw_content": src.extracted,
        "format": ResourceFormat.IMAGE if src.kind == "image" else ResourceFormat.PDF,
    }

"""In-memory memory store for unit tests and offline evals. Same contract as Postgres:
workspace-bound, and a transaction commits all together or not at all (it works on a copy and
swaps it in on success). ``faults`` injects a failure on the N-th call of a write method."""

import asyncio
import copy
import math
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime

from secondmind.core import (
    EntityKind,
    ItemStatus,
    KeyKind,
    Kind,
    TriggerOn,
    TriggerState,
    VocabKind,
    WorkspaceScope,
    new_id,
)
from secondmind.memory.records import (
    CategoryRecord,
    EntityRecord,
    HeldWriteRecord,
    ItemEntityRecord,
    ItemRecord,
    KeyRecord,
    LinkRecord,
    RelationRecord,
    TriggerRecord,
    VersionRecord,
    VocabRecord,
    WriteLogRecord,
)


class InjectedFaultError(RuntimeError):
    pass


@dataclass
class _Tables:
    items: dict[uuid.UUID, ItemRecord] = field(default_factory=dict)
    entities: dict[uuid.UUID, EntityRecord] = field(default_factory=dict)
    item_entities: dict[uuid.UUID, ItemEntityRecord] = field(default_factory=dict)
    links: dict[uuid.UUID, LinkRecord] = field(default_factory=dict)
    relations: dict[uuid.UUID, RelationRecord] = field(default_factory=dict)
    triggers: dict[uuid.UUID, TriggerRecord] = field(default_factory=dict)
    categories: dict[str, CategoryRecord] = field(default_factory=dict)
    vocab: dict[tuple[VocabKind, str], VocabRecord] = field(default_factory=dict)
    keys: dict[uuid.UUID, KeyRecord] = field(default_factory=dict)
    write_log: list[WriteLogRecord] = field(default_factory=list)
    held: dict[uuid.UUID, HeldWriteRecord] = field(default_factory=dict)
    versions: list[VersionRecord] = field(default_factory=list)


class InMemoryMemory:
    """All workspaces' memory in one process. ``store(scope)`` binds one workspace."""

    def __init__(self) -> None:
        self._workspaces: dict[uuid.UUID, _Tables] = {}
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}
        self.faults: dict[str, int] = {}
        self.calls: dict[str, int] = {}

    def store(self, scope: WorkspaceScope) -> "InMemoryMemoryStore":
        return InMemoryMemoryStore(self, scope.workspace_id)

    def tables(self, workspace_id: uuid.UUID) -> _Tables:
        if workspace_id not in self._workspaces:
            tables = _Tables()
            me = EntityRecord(
                id=new_id(),
                workspace_id=workspace_id,
                kind=EntityKind.SELF,
                name="me",
                created_at=datetime.fromtimestamp(0).astimezone(),
                updated_at=datetime.fromtimestamp(0).astimezone(),
                created_by_turn_id=None,
                updated_by_turn_id=None,
            )
            tables.entities[me.id] = me
            self._workspaces[workspace_id] = tables
            self._locks[workspace_id] = asyncio.Lock()
        return self._workspaces[workspace_id]

    def lock(self, workspace_id: uuid.UUID) -> asyncio.Lock:
        self.tables(workspace_id)
        return self._locks[workspace_id]

    def commit(self, workspace_id: uuid.UUID, tables: _Tables) -> None:
        self._workspaces[workspace_id] = tables

    def fault(self, method: str) -> None:
        self.calls[method] = self.calls.get(method, 0) + 1
        if self.faults.get(method) == self.calls[method]:
            raise InjectedFaultError(f"injected failure in {method} (call {self.calls[method]})")


class InMemoryMemoryStore:
    def __init__(self, db: InMemoryMemory, workspace_id: uuid.UUID) -> None:
        self._db = db
        self._ws = workspace_id

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["InMemoryTx"]:
        async with self._db.lock(self._ws):
            working = copy.copy(self._db.tables(self._ws))
            for name in working.__dataclass_fields__:
                setattr(working, name, copy.copy(getattr(working, name)))
            yield InMemoryTx(self._db, self._ws, working)
            self._db.commit(self._ws, working)


class InMemoryTx:
    def __init__(self, db: InMemoryMemory, workspace_id: uuid.UUID, tables: _Tables) -> None:
        self._db = db
        self._ws = workspace_id
        self._t = tables

    # ------------------------------------------------------------------ reads
    async def get_item(self, item_id: uuid.UUID) -> ItemRecord | None:
        return self._t.items.get(item_id)

    async def get_items(self, item_ids: Sequence[uuid.UUID]) -> list[ItemRecord]:
        return [self._t.items[i] for i in item_ids if i in self._t.items]

    async def find_items(
        self,
        *,
        kinds: Sequence[Kind] | None = None,
        states: Sequence[str] | None = None,
        entity_id: uuid.UUID | None = None,
        subject_entity_id: uuid.UUID | None = None,
        predicate: str | None = None,
        current_only: bool = False,
        in_core: bool | None = None,
        limit: int = 200,
    ) -> list[ItemRecord]:
        linked: set[uuid.UUID] | None = None
        if entity_id is not None:
            linked = {r.item_id for r in self._t.item_entities.values() if r.entity_id == entity_id}
        out = []
        for item in self._t.items.values():
            if item.status is not ItemStatus.ACTIVE:
                continue
            if kinds is not None and item.kind not in kinds:
                continue
            if states is not None and item.state not in states:
                continue
            if linked is not None and item.id not in linked and item.subject_entity_id != entity_id:
                continue
            if subject_entity_id is not None and item.subject_entity_id != subject_entity_id:
                continue
            if predicate is not None and item.predicate != predicate:
                continue
            if current_only and item.valid_to is not None:
                continue
            if in_core is not None and item.in_core != in_core:
                continue
            out.append(item)
        out.sort(key=lambda i: (i.created_at, i.id), reverse=True)
        return out[:limit]

    async def similar_items(
        self, vector: Sequence[float], *, limit: int, model: str
    ) -> list[tuple[uuid.UUID, float]]:
        best: dict[uuid.UUID, float] = {}
        for key in self._t.keys.values():
            item = self._t.items.get(key.item_id)
            if key.embedding is None or key.embedding_model != model or item is None:
                continue
            if item.status is not ItemStatus.ACTIVE:
                continue
            score = _cosine(vector, key.embedding)
            best[key.item_id] = max(score, best.get(key.item_id, -1.0))
        return sorted(best.items(), key=lambda kv: kv[1], reverse=True)[:limit]

    async def get_entity(self, entity_id: uuid.UUID) -> EntityRecord | None:
        return self._t.entities.get(entity_id)

    async def list_entities(self, kinds: Sequence[EntityKind] | None = None) -> list[EntityRecord]:
        return [
            e
            for e in self._t.entities.values()
            if e.status == "active" and (kinds is None or e.kind in kinds)
        ]

    async def self_entity(self) -> EntityRecord:
        return next(e for e in self._t.entities.values() if e.kind is EntityKind.SELF)

    async def item_entities(self, item_ids: Sequence[uuid.UUID]) -> list[ItemEntityRecord]:
        wanted = set(item_ids)
        return [r for r in self._t.item_entities.values() if r.item_id in wanted]

    async def entity_items(self, entity_id: uuid.UUID) -> list[uuid.UUID]:
        ids = {r.item_id for r in self._t.item_entities.values() if r.entity_id == entity_id}
        ids |= {i.id for i in self._t.items.values() if i.subject_entity_id == entity_id}
        return sorted(
            i for i in ids if i in self._t.items and self._t.items[i].status is ItemStatus.ACTIVE
        )

    async def links(self, item_ids: Sequence[uuid.UUID]) -> list[LinkRecord]:
        wanted = set(item_ids)
        return [
            lk
            for lk in self._t.links.values()
            if lk.src_item_id in wanted or lk.dst_item_id in wanted
        ]

    async def get_link(self, link_id: uuid.UUID) -> LinkRecord | None:
        return self._t.links.get(link_id)

    async def get_item_entity(self, row_id: uuid.UUID) -> ItemEntityRecord | None:
        return self._t.item_entities.get(row_id)

    async def get_relation(self, relation_id: uuid.UUID) -> RelationRecord | None:
        return self._t.relations.get(relation_id)

    async def relations(self, entity_ids: Sequence[uuid.UUID]) -> list[RelationRecord]:
        wanted = set(entity_ids)
        return [
            r
            for r in self._t.relations.values()
            if r.src_entity_id in wanted or r.dst_entity_id in wanted
        ]

    async def get_trigger(self, trigger_id: uuid.UUID) -> TriggerRecord | None:
        return self._t.triggers.get(trigger_id)

    async def triggers(self, item_ids: Sequence[uuid.UUID]) -> list[TriggerRecord]:
        wanted = set(item_ids)
        return [t for t in self._t.triggers.values() if t.item_id in wanted]

    async def categories(self) -> list[CategoryRecord]:
        return sorted(self._t.categories.values(), key=lambda c: c.slug)

    async def vocab(self, vocab: VocabKind | None = None) -> list[VocabRecord]:
        return sorted(
            (v for v in self._t.vocab.values() if vocab is None or v.vocab is vocab),
            key=lambda v: (v.vocab, v.slug),
        )

    async def write_log(self, turn_id: uuid.UUID) -> list[WriteLogRecord]:
        return sorted((r for r in self._t.write_log if r.turn_id == turn_id), key=lambda r: r.seq)

    async def get_held_write(self, held_id: uuid.UUID) -> HeldWriteRecord | None:
        return self._t.held.get(held_id)

    async def held_writes(self, *, status: str | None = None) -> list[HeldWriteRecord]:
        return [h for h in self._t.held.values() if status is None or h.status == status]

    async def cached_embeddings(self, hashes: Sequence[str], model: str) -> dict[str, list[float]]:
        wanted = set(hashes)
        return {
            k.content_hash: k.embedding
            for k in self._t.keys.values()
            if k.content_hash in wanted and k.embedding is not None and k.embedding_model == model
        }

    async def keys(self, item_ids: Sequence[uuid.UUID]) -> list[KeyRecord]:
        wanted = set(item_ids)
        return [k for k in self._t.keys.values() if k.item_id in wanted]

    async def expired_quick(self, now: datetime) -> list[ItemRecord]:
        return [
            i
            for i in self._t.items.values()
            if i.status is ItemStatus.ACTIVE
            and i.in_quick
            and i.quick_until is not None
            and i.quick_until < now
        ]

    async def passed_triggers(self, now: datetime) -> list[TriggerRecord]:
        return [
            t
            for t in self._t.triggers.values()
            if t.on is TriggerOn.TIME
            and t.state is TriggerState.PENDING
            and t.fires_at is not None
            and t.fires_at < now
        ]

    # ------------------------------------------------------------------ writes
    async def insert_item(self, record: ItemRecord) -> None:
        self._db.fault("insert_item")
        self._check(record.workspace_id)
        self._t.items[record.id] = record

    async def replace_item(self, record: ItemRecord) -> None:
        self._db.fault("replace_item")
        self._t.items[record.id] = record

    async def insert_entity(self, record: EntityRecord) -> None:
        self._db.fault("insert_entity")
        self._check(record.workspace_id)
        self._t.entities[record.id] = record

    async def replace_entity(self, record: EntityRecord) -> None:
        self._t.entities[record.id] = record

    async def insert_item_entity(self, record: ItemEntityRecord) -> None:
        self._t.item_entities[record.id] = record

    async def delete_item_entity(self, row_id: uuid.UUID) -> None:
        self._t.item_entities.pop(row_id, None)

    async def insert_link(self, record: LinkRecord) -> None:
        self._t.links[record.id] = record

    async def delete_link(self, link_id: uuid.UUID) -> None:
        self._t.links.pop(link_id, None)

    async def insert_relation(self, record: RelationRecord) -> None:
        self._t.relations[record.id] = record

    async def delete_relation(self, relation_id: uuid.UUID) -> None:
        self._t.relations.pop(relation_id, None)

    async def insert_trigger(self, record: TriggerRecord) -> None:
        self._t.triggers[record.id] = record

    async def replace_trigger(self, record: TriggerRecord) -> None:
        self._t.triggers[record.id] = record

    async def upsert_category(self, record: CategoryRecord) -> CategoryRecord:
        return self._t.categories.setdefault(record.slug, record)

    async def upsert_vocab(self, record: VocabRecord) -> VocabRecord:
        return self._t.vocab.setdefault((record.vocab, record.slug), record)

    async def insert_write_log(self, rows: Sequence[WriteLogRecord]) -> None:
        self._db.fault("insert_write_log")
        self._t.write_log.extend(rows)

    async def insert_held_write(self, record: HeldWriteRecord) -> None:
        self._t.held[record.id] = record

    async def resolve_held_write(
        self, held_id: uuid.UUID, *, status: str, turn_id: uuid.UUID | None, at: datetime
    ) -> None:
        held = self._t.held[held_id]
        self._t.held[held_id] = held.model_copy(
            update={"status": status, "resolved_turn_id": turn_id, "resolved_at": at}
        )

    async def insert_version(self, record: VersionRecord) -> None:
        self._t.versions.append(record)

    async def next_version(self, item_id: uuid.UUID) -> int:
        return 1 + sum(1 for v in self._t.versions if v.item_id == item_id)

    async def replace_keys(
        self, item_id: uuid.UUID, kinds: Sequence[KeyKind], keys: Sequence[KeyRecord]
    ) -> None:
        drop = set(kinds)
        self._t.keys = {
            k: v
            for k, v in self._t.keys.items()
            if not (v.item_id == item_id and v.key_kind in drop)
        }
        for key in keys:
            self._t.keys[key.id] = key

    def _check(self, workspace_id: uuid.UUID) -> None:
        if workspace_id != self._ws:
            raise PermissionError("row belongs to another workspace")


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)

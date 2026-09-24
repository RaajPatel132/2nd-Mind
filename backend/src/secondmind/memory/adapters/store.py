"""Postgres implementation of the memory store. Every transaction runs through
``Database.workspace(scope)``, so RLS scopes every statement to one workspace."""

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Table, and_, delete, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from secondmind.core import (
    EntityKind,
    KeyKind,
    Kind,
    VocabKind,
    WorkspaceScope,
)
from secondmind.memory.adapters.db import Database
from secondmind.memory.adapters.tables import (
    CategoryRow,
    EntityRelationRow,
    EntityRow,
    HeldWriteRow,
    ItemVersionRow,
    MemoryEntityRow,
    MemoryItemRow,
    MemoryKeyRow,
    MemoryLinkRow,
    TriggerRow,
    VocabTermRow,
    WriteLogRow,
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

_ITEMS: Table = MemoryItemRow.__table__  # type: ignore[assignment]
_ENTITIES: Table = EntityRow.__table__  # type: ignore[assignment]
_ITEM_ENTITIES: Table = MemoryEntityRow.__table__  # type: ignore[assignment]
_LINKS: Table = MemoryLinkRow.__table__  # type: ignore[assignment]
_RELATIONS: Table = EntityRelationRow.__table__  # type: ignore[assignment]
_TRIGGERS: Table = TriggerRow.__table__  # type: ignore[assignment]
_CATEGORIES: Table = CategoryRow.__table__  # type: ignore[assignment]
_VOCAB: Table = VocabTermRow.__table__  # type: ignore[assignment]
_KEYS: Table = MemoryKeyRow.__table__  # type: ignore[assignment]
_LOG: Table = WriteLogRow.__table__  # type: ignore[assignment]
_HELD: Table = HeldWriteRow.__table__  # type: ignore[assignment]
_VERSIONS: Table = ItemVersionRow.__table__  # type: ignore[assignment]


def _values(table: Table, record: BaseModel, **extra: Any) -> dict[str, Any]:
    """Column values for a record: JSON columns get JSON-safe values, the rest Python ones."""
    python = record.model_dump()
    as_json = record.model_dump(mode="json")
    out: dict[str, Any] = {}
    for column in table.columns:
        name = column.key
        if name in extra:
            out[name] = extra[name]
        elif name in python:
            out[name] = as_json[name] if isinstance(column.type, JSONB) else python[name]
    return out


def _cols(table: Table, record_type: type[BaseModel]) -> list[Any]:
    return [c for c in table.columns if c.key in record_type.model_fields]


class SqlMemoryStore:
    def __init__(self, db: Database, scope: WorkspaceScope) -> None:
        self._db = db
        self._scope = scope

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["SqlMemoryTx"]:
        async with self._db.workspace(self._scope) as session:
            yield SqlMemoryTx(session, self._scope.workspace_id)


class SqlMemoryTx:
    def __init__(self, session: AsyncSession, workspace_id: uuid.UUID) -> None:
        self._s = session
        self._ws = workspace_id

    async def _all[R: BaseModel](
        self, record: type[R], table: Table, *where: Any, order: Any = None
    ) -> list[R]:
        stmt = select(*_cols(table, record)).where(*where)
        if order is not None:
            stmt = stmt.order_by(order)
        rows = (await self._s.execute(stmt)).mappings().all()
        return [record.model_validate(dict(r)) for r in rows]

    async def _one[R: BaseModel](self, record: type[R], table: Table, *where: Any) -> R | None:
        found = await self._all(record, table, *where)
        return found[0] if found else None

    # ------------------------------------------------------------------ reads
    async def get_item(self, item_id: uuid.UUID) -> ItemRecord | None:
        return await self._one(ItemRecord, _ITEMS, _ITEMS.c.id == item_id)

    async def get_items(self, item_ids: Sequence[uuid.UUID]) -> list[ItemRecord]:
        if not item_ids:
            return []
        return await self._all(ItemRecord, _ITEMS, _ITEMS.c.id.in_(list(item_ids)))

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
        c = _ITEMS.c
        where: list[Any] = [c.status == "active"]
        if kinds is not None:
            where.append(c.kind.in_([k.value for k in kinds]))
        if states is not None:
            where.append(c.state.in_(list(states)))
        if entity_id is not None:
            linked = select(_ITEM_ENTITIES.c.item_id).where(_ITEM_ENTITIES.c.entity_id == entity_id)
            where.append(or_(c.id.in_(linked), c.subject_entity_id == entity_id))
        if subject_entity_id is not None:
            where.append(c.subject_entity_id == subject_entity_id)
        if predicate is not None:
            where.append(c.predicate == predicate)
        if current_only:
            where.append(c.valid_to.is_(None))
        if in_core is not None:
            where.append(c.in_core.is_(in_core))
        stmt = (
            select(*_cols(_ITEMS, ItemRecord))
            .where(and_(*where))
            .order_by(c.created_at.desc(), c.id.desc())
            .limit(limit)
        )
        rows = (await self._s.execute(stmt)).mappings().all()
        return [ItemRecord.model_validate(dict(r)) for r in rows]

    async def similar_items(
        self, vector: Sequence[float], *, limit: int, model: str
    ) -> list[tuple[uuid.UUID, float]]:
        literal = "[" + ",".join(f"{float(v):.7g}" for v in vector) + "]"
        rows = await self._s.execute(
            text(
                """
                SELECT k.item_id, max(1 - (k.embedding <=> CAST(:q AS vector))) AS score
                FROM memory_keys k JOIN memory_items i ON i.id = k.item_id
                WHERE k.embedding IS NOT NULL AND k.embedding_model = :model
                  AND i.status = 'active'
                GROUP BY k.item_id ORDER BY score DESC LIMIT :limit
                """
            ),
            {"q": literal, "model": model, "limit": limit},
        )
        return [(r[0], float(r[1])) for r in rows]

    async def get_entity(self, entity_id: uuid.UUID) -> EntityRecord | None:
        return await self._one(EntityRecord, _ENTITIES, _ENTITIES.c.id == entity_id)

    async def list_entities(self, kinds: Sequence[EntityKind] | None = None) -> list[EntityRecord]:
        where: list[Any] = [_ENTITIES.c.status == "active"]
        if kinds is not None:
            where.append(_ENTITIES.c.kind.in_([k.value for k in kinds]))
        return await self._all(EntityRecord, _ENTITIES, *where, order=_ENTITIES.c.created_at)

    async def self_entity(self) -> EntityRecord:
        found = await self._one(EntityRecord, _ENTITIES, _ENTITIES.c.kind == EntityKind.SELF.value)
        if found is None:
            raise LookupError("workspace has no self entity")
        return found

    async def item_entities(self, item_ids: Sequence[uuid.UUID]) -> list[ItemEntityRecord]:
        if not item_ids:
            return []
        return await self._all(
            ItemEntityRecord, _ITEM_ENTITIES, _ITEM_ENTITIES.c.item_id.in_(list(item_ids))
        )

    async def entity_items(self, entity_id: uuid.UUID) -> list[uuid.UUID]:
        linked = select(_ITEM_ENTITIES.c.item_id).where(_ITEM_ENTITIES.c.entity_id == entity_id)
        stmt = select(_ITEMS.c.id).where(
            _ITEMS.c.status == "active",
            or_(_ITEMS.c.id.in_(linked), _ITEMS.c.subject_entity_id == entity_id),
        )
        return list((await self._s.execute(stmt)).scalars())

    async def links(self, item_ids: Sequence[uuid.UUID]) -> list[LinkRecord]:
        if not item_ids:
            return []
        ids = list(item_ids)
        return await self._all(
            LinkRecord, _LINKS, or_(_LINKS.c.src_item_id.in_(ids), _LINKS.c.dst_item_id.in_(ids))
        )

    async def get_link(self, link_id: uuid.UUID) -> LinkRecord | None:
        return await self._one(LinkRecord, _LINKS, _LINKS.c.id == link_id)

    async def get_item_entity(self, row_id: uuid.UUID) -> ItemEntityRecord | None:
        return await self._one(ItemEntityRecord, _ITEM_ENTITIES, _ITEM_ENTITIES.c.id == row_id)

    async def get_relation(self, relation_id: uuid.UUID) -> RelationRecord | None:
        return await self._one(RelationRecord, _RELATIONS, _RELATIONS.c.id == relation_id)

    async def relations(self, entity_ids: Sequence[uuid.UUID]) -> list[RelationRecord]:
        if not entity_ids:
            return []
        ids = list(entity_ids)
        return await self._all(
            RelationRecord,
            _RELATIONS,
            or_(_RELATIONS.c.src_entity_id.in_(ids), _RELATIONS.c.dst_entity_id.in_(ids)),
        )

    async def get_trigger(self, trigger_id: uuid.UUID) -> TriggerRecord | None:
        return await self._one(TriggerRecord, _TRIGGERS, _TRIGGERS.c.id == trigger_id)

    async def triggers(self, item_ids: Sequence[uuid.UUID]) -> list[TriggerRecord]:
        if not item_ids:
            return []
        return await self._all(TriggerRecord, _TRIGGERS, _TRIGGERS.c.item_id.in_(list(item_ids)))

    async def categories(self) -> list[CategoryRecord]:
        return await self._all(CategoryRecord, _CATEGORIES, order=_CATEGORIES.c.slug)

    async def vocab(self, vocab: VocabKind | None = None) -> list[VocabRecord]:
        where = [] if vocab is None else [_VOCAB.c.vocab == vocab.value]
        return await self._all(VocabRecord, _VOCAB, *where, order=_VOCAB.c.slug)

    async def write_log(self, turn_id: uuid.UUID) -> list[WriteLogRecord]:
        return await self._all(WriteLogRecord, _LOG, _LOG.c.turn_id == turn_id, order=_LOG.c.seq)

    async def get_held_write(self, held_id: uuid.UUID) -> HeldWriteRecord | None:
        return await self._one(HeldWriteRecord, _HELD, _HELD.c.id == held_id)

    async def held_writes(self, *, status: str | None = None) -> list[HeldWriteRecord]:
        where = [] if status is None else [_HELD.c.status == status]
        return await self._all(HeldWriteRecord, _HELD, *where, order=_HELD.c.created_at)

    async def cached_embeddings(self, hashes: Sequence[str], model: str) -> dict[str, list[float]]:
        if not hashes:
            return {}
        stmt = (
            select(_KEYS.c.content_hash, _KEYS.c.embedding)
            .where(
                _KEYS.c.content_hash.in_(list(hashes)),
                _KEYS.c.embedding_model == model,
                _KEYS.c.embedding.is_not(None),
            )
            .distinct(_KEYS.c.content_hash)
        )
        return {row[0]: row[1] for row in await self._s.execute(stmt)}

    async def keys(self, item_ids: Sequence[uuid.UUID]) -> list[KeyRecord]:
        if not item_ids:
            return []
        return await self._all(KeyRecord, _KEYS, _KEYS.c.item_id.in_(list(item_ids)))

    async def expired_quick(self, now: datetime) -> list[ItemRecord]:
        c = _ITEMS.c
        return await self._all(
            ItemRecord,
            _ITEMS,
            c.status == "active",
            c.in_quick.is_(True),
            c.quick_until.is_not(None),
            c.quick_until < now,
        )

    async def passed_triggers(self, now: datetime) -> list[TriggerRecord]:
        c = _TRIGGERS.c
        return await self._all(
            TriggerRecord,
            _TRIGGERS,
            c.on == "time",
            c.state == "pending",
            c.fires_at.is_not(None),
            c.fires_at < now,
        )

    # ------------------------------------------------------------------ writes
    async def insert_item(self, record: ItemRecord) -> None:
        await self._s.execute(insert(_ITEMS).values(_values(_ITEMS, record)))

    async def replace_item(self, record: ItemRecord) -> None:
        values = _values(_ITEMS, record)
        values.pop("id")
        await self._s.execute(update(_ITEMS).where(_ITEMS.c.id == record.id).values(values))

    async def insert_entity(self, record: EntityRecord) -> None:
        await self._s.execute(insert(_ENTITIES).values(_values(_ENTITIES, record)))

    async def replace_entity(self, record: EntityRecord) -> None:
        values = _values(_ENTITIES, record)
        values.pop("id")
        await self._s.execute(update(_ENTITIES).where(_ENTITIES.c.id == record.id).values(values))

    async def insert_item_entity(self, record: ItemEntityRecord) -> None:
        await self._s.execute(insert(_ITEM_ENTITIES).values(_values(_ITEM_ENTITIES, record)))

    async def delete_item_entity(self, row_id: uuid.UUID) -> None:
        await self._s.execute(delete(_ITEM_ENTITIES).where(_ITEM_ENTITIES.c.id == row_id))

    async def insert_link(self, record: LinkRecord) -> None:
        await self._s.execute(insert(_LINKS).values(_values(_LINKS, record)))

    async def delete_link(self, link_id: uuid.UUID) -> None:
        await self._s.execute(delete(_LINKS).where(_LINKS.c.id == link_id))

    async def insert_relation(self, record: RelationRecord) -> None:
        await self._s.execute(insert(_RELATIONS).values(_values(_RELATIONS, record)))

    async def delete_relation(self, relation_id: uuid.UUID) -> None:
        await self._s.execute(delete(_RELATIONS).where(_RELATIONS.c.id == relation_id))

    async def insert_trigger(self, record: TriggerRecord) -> None:
        await self._s.execute(insert(_TRIGGERS).values(_values(_TRIGGERS, record)))

    async def replace_trigger(self, record: TriggerRecord) -> None:
        values = _values(_TRIGGERS, record)
        values.pop("id")
        await self._s.execute(update(_TRIGGERS).where(_TRIGGERS.c.id == record.id).values(values))

    async def upsert_category(self, record: CategoryRecord) -> CategoryRecord:
        await self._s.execute(
            pg_insert(_CATEGORIES)
            .values(_values(_CATEGORIES, record))
            .on_conflict_do_nothing(index_elements=["workspace_id", "slug"])
        )
        stored = await self._one(CategoryRecord, _CATEGORIES, _CATEGORIES.c.slug == record.slug)
        if stored is None:
            raise LookupError(f"category {record.slug!r} vanished")
        return stored

    async def upsert_vocab(self, record: VocabRecord) -> VocabRecord:
        await self._s.execute(
            pg_insert(_VOCAB)
            .values(_values(_VOCAB, record))
            .on_conflict_do_nothing(index_elements=["workspace_id", "vocab", "slug"])
        )
        stored = await self._one(
            VocabRecord, _VOCAB, _VOCAB.c.vocab == record.vocab.value, _VOCAB.c.slug == record.slug
        )
        if stored is None:
            raise LookupError(f"vocab term {record.slug!r} vanished")
        return stored

    async def insert_write_log(self, rows: Sequence[WriteLogRecord]) -> None:
        if rows:
            await self._s.execute(
                insert(_LOG), [_values(_LOG, r, workspace_id=self._ws) for r in rows]
            )

    async def insert_held_write(self, record: HeldWriteRecord) -> None:
        await self._s.execute(insert(_HELD).values(_values(_HELD, record)))

    async def resolve_held_write(
        self, held_id: uuid.UUID, *, status: str, turn_id: uuid.UUID | None, at: datetime
    ) -> None:
        await self._s.execute(
            update(_HELD)
            .where(_HELD.c.id == held_id)
            .values(status=status, resolved_turn_id=turn_id, resolved_at=at)
        )

    async def insert_version(self, record: VersionRecord) -> None:
        await self._s.execute(
            insert(_VERSIONS).values(_values(_VERSIONS, record, workspace_id=self._ws))
        )

    async def next_version(self, item_id: uuid.UUID) -> int:
        stmt = select(func.coalesce(func.max(_VERSIONS.c.version), 0)).where(
            _VERSIONS.c.item_id == item_id
        )
        return int((await self._s.execute(stmt)).scalar_one()) + 1

    async def replace_keys(
        self, item_id: uuid.UUID, kinds: Sequence[KeyKind], keys: Sequence[KeyRecord]
    ) -> None:
        await self._s.execute(
            delete(_KEYS).where(
                _KEYS.c.item_id == item_id, _KEYS.c.key_kind.in_([k.value for k in kinds])
            )
        )
        if keys:
            await self._s.execute(insert(_KEYS), [_values(_KEYS, k) for k in keys])


async def embedding_dimensions(db: Database) -> int | None:
    """The migrated dimension of ``memory_keys.embedding`` (None before migration 0002)."""
    async with db.engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT a.atttypmod FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                    "WHERE c.relname = 'memory_keys' AND a.attname = 'embedding' "
                    "AND NOT a.attisdropped"
                )
            )
        ).one_or_none()
    return None if row is None else int(row[0])

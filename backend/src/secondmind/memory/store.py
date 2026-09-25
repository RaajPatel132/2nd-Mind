"""The memory store port. Internal to ``memory``: other modules read through ``MemoryReader`` and
write only through ``MemoryWriter`` (an import-linter contract forbids importing this module
from outside ``memory``). Implementations: ``memory.adapters`` (Postgres, and in-memory for
tests and evals).

A ``MemoryTx`` is one transaction bound to one workspace: everything done through it commits
together or not at all (NFR-6.1).
"""

import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Protocol

from secondmind.core import EntityKind, KeyKind, Kind, VocabKind, WorkspaceScope
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


class MemoryTx(Protocol):
    # ------------------------------------------------------------------ reads
    async def get_item(self, item_id: uuid.UUID) -> ItemRecord | None: ...

    async def get_items(self, item_ids: Sequence[uuid.UUID]) -> list[ItemRecord]: ...

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
        """Active items matching every given filter, newest first. ``current_only`` means
        ``valid_to IS NULL`` (the partial index serves subject + predicate + current)."""
        ...

    async def similar_items(
        self, vector: Sequence[float], *, limit: int, model: str
    ) -> list[tuple[uuid.UUID, float]]:
        """Active items ranked by their best key's cosine similarity (exact scan until S3)."""
        ...

    async def get_entity(self, entity_id: uuid.UUID) -> EntityRecord | None: ...

    async def list_entities(self, kinds: Sequence[EntityKind] | None = None) -> list[EntityRecord]:
        """Active entities."""
        ...

    async def self_entity(self) -> EntityRecord: ...

    async def item_entities(self, item_ids: Sequence[uuid.UUID]) -> list[ItemEntityRecord]: ...

    async def entity_items(self, entity_id: uuid.UUID) -> list[uuid.UUID]:
        """Ids of active items linked to the entity (any role, or as subject)."""
        ...

    async def links(self, item_ids: Sequence[uuid.UUID]) -> list[LinkRecord]:
        """Links where any given item is the source or the destination."""
        ...

    async def get_link(self, link_id: uuid.UUID) -> LinkRecord | None: ...

    async def get_item_entity(self, row_id: uuid.UUID) -> ItemEntityRecord | None: ...

    async def get_relation(self, relation_id: uuid.UUID) -> RelationRecord | None: ...

    async def relations(self, entity_ids: Sequence[uuid.UUID]) -> list[RelationRecord]: ...

    async def get_trigger(self, trigger_id: uuid.UUID) -> TriggerRecord | None: ...

    async def triggers(self, item_ids: Sequence[uuid.UUID]) -> list[TriggerRecord]: ...

    async def categories(self) -> list[CategoryRecord]: ...

    async def vocab(self, vocab: VocabKind | None = None) -> list[VocabRecord]: ...

    async def write_log(self, turn_id: uuid.UUID) -> list[WriteLogRecord]: ...

    async def get_held_write(self, held_id: uuid.UUID) -> HeldWriteRecord | None: ...

    async def held_writes(self, *, status: str | None = None) -> list[HeldWriteRecord]: ...

    async def cached_embeddings(
        self, hashes: Sequence[str], model: str
    ) -> dict[str, list[float]]: ...

    async def keys(self, item_ids: Sequence[uuid.UUID]) -> list[KeyRecord]: ...

    async def expired_quick(self, now: datetime) -> list[ItemRecord]: ...

    async def passed_triggers(self, now: datetime) -> list[TriggerRecord]: ...

    async def frequent_items(self, since: datetime, min_turns: int) -> list[uuid.UUID]:
        """Active items cited in at least ``min_turns`` recall turns since ``since`` (FR-6.7)."""
        ...

    # ------------------------------------------------------------------ bookkeeping, not memory
    async def record_access(
        self,
        turn_id: uuid.UUID,
        at: datetime,
        retrieved: Sequence[uuid.UUID],
        cited: Sequence[uuid.UUID],
    ) -> None:
        """Append ``item_access`` rows and bump ``access_count`` / ``last_accessed_at``. Not a
        memory write: no write log, no version, ``updated_at`` untouched."""
        ...

    # ------------------------------------------------------------------ writes (writer only)
    async def insert_item(self, record: ItemRecord) -> None: ...

    async def replace_item(self, record: ItemRecord) -> None: ...

    async def insert_entity(self, record: EntityRecord) -> None: ...

    async def replace_entity(self, record: EntityRecord) -> None: ...

    async def insert_item_entity(self, record: ItemEntityRecord) -> None: ...

    async def delete_item_entity(self, row_id: uuid.UUID) -> None: ...

    async def insert_link(self, record: LinkRecord) -> None: ...

    async def delete_link(self, link_id: uuid.UUID) -> None: ...

    async def insert_relation(self, record: RelationRecord) -> None: ...

    async def delete_relation(self, relation_id: uuid.UUID) -> None: ...

    async def insert_trigger(self, record: TriggerRecord) -> None: ...

    async def replace_trigger(self, record: TriggerRecord) -> None: ...

    async def upsert_category(self, record: CategoryRecord) -> CategoryRecord:
        """Insert unless the slug exists; returns the stored row either way."""
        ...

    async def upsert_vocab(self, record: VocabRecord) -> VocabRecord: ...

    async def insert_write_log(self, rows: Sequence[WriteLogRecord]) -> None: ...

    async def insert_held_write(self, record: HeldWriteRecord) -> None: ...

    async def resolve_held_write(
        self, held_id: uuid.UUID, *, status: str, turn_id: uuid.UUID | None, at: datetime
    ) -> None: ...

    async def insert_version(self, record: VersionRecord) -> None: ...

    async def next_version(self, item_id: uuid.UUID) -> int: ...

    async def replace_keys(
        self,
        item_id: uuid.UUID,
        kinds: Sequence[KeyKind],
        keys: Sequence[KeyRecord],
    ) -> None:
        """Delete the item's keys of ``kinds`` and insert ``keys`` (derived data, not logged)."""
        ...


class MemoryStore(Protocol):
    """Opens workspace-bound transactions."""

    def transaction(self) -> AbstractAsyncContextManager[MemoryTx]: ...


MemoryStoreFactory = Callable[[WorkspaceScope], MemoryStore]


def jsonable(value: Any) -> Any:
    """Plain JSON value for snapshots (datetimes and uuids as strings)."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    return value

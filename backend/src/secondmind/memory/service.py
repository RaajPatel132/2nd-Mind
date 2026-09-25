"""What other modules use: a read-only reader, a writer per turn, the key indexer, undo and
held-write confirmation. Write methods of the store never leave this module."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from secondmind.core import (
    EntityKind,
    ItemStatus,
    Kind,
    NotFoundError,
    TriggerState,
    ValidationFailedError,
    VocabKind,
    WorkspaceScope,
)
from secondmind.memory.core_layer import CoreView, render_core
from secondmind.memory.keys import Embedder, KeyIndexer
from secondmind.memory.ops import CreateItem, Op, SetTriggerState, UpdateItem, load_op
from secondmind.memory.quick import quick_layer
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
    VocabRecord,
    WriteLogRecord,
)
from secondmind.memory.store import MemoryStore, MemoryStoreFactory
from secondmind.memory.undo import plan_undo
from secondmind.memory.writer import CommitResult, Emit, MemoryWriter, WriterSettings, WriterTurn


@dataclass(frozen=True, slots=True)
class MemorySettings:
    bulk_threshold: int = 5
    core_token_budget: int = 1_500
    quick_horizon_days: int = 30
    quick_recent_days: int = 7
    verbal_keys_enabled: bool = True


class MemoryReader:
    """Read-only access to one workspace's memory. Each call is one short transaction."""

    def __init__(self, store: MemoryStore, settings: MemorySettings) -> None:
        self._store = store
        self._settings = settings

    async def item(self, item_id: uuid.UUID) -> ItemRecord | None:
        async with self._store.transaction() as tx:
            return await tx.get_item(item_id)

    async def items(self, item_ids: Sequence[uuid.UUID]) -> list[ItemRecord]:
        async with self._store.transaction() as tx:
            return await tx.get_items(item_ids)

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
        async with self._store.transaction() as tx:
            return await tx.find_items(
                kinds=kinds,
                states=states,
                entity_id=entity_id,
                subject_entity_id=subject_entity_id,
                predicate=predicate,
                current_only=current_only,
                in_core=in_core,
                limit=limit,
            )

    async def similar_items(
        self, vector: Sequence[float], *, limit: int, model: str
    ) -> list[tuple[uuid.UUID, float]]:
        async with self._store.transaction() as tx:
            return await tx.similar_items(vector, limit=limit, model=model)

    async def self_entity(self) -> EntityRecord:
        async with self._store.transaction() as tx:
            return await tx.self_entity()

    async def entity(self, entity_id: uuid.UUID) -> EntityRecord | None:
        async with self._store.transaction() as tx:
            return await tx.get_entity(entity_id)

    async def entities(self, kinds: Sequence[EntityKind] | None = None) -> list[EntityRecord]:
        async with self._store.transaction() as tx:
            return await tx.list_entities(kinds)

    async def item_entities(self, item_ids: Sequence[uuid.UUID]) -> list[ItemEntityRecord]:
        async with self._store.transaction() as tx:
            return await tx.item_entities(item_ids)

    async def links(self, item_ids: Sequence[uuid.UUID]) -> list[LinkRecord]:
        async with self._store.transaction() as tx:
            return await tx.links(item_ids)

    async def relations(self, entity_ids: Sequence[uuid.UUID]) -> list[RelationRecord]:
        async with self._store.transaction() as tx:
            return await tx.relations(entity_ids)

    async def triggers(self, item_ids: Sequence[uuid.UUID]) -> list[TriggerRecord]:
        async with self._store.transaction() as tx:
            return await tx.triggers(item_ids)

    async def categories(self) -> list[CategoryRecord]:
        async with self._store.transaction() as tx:
            return await tx.categories()

    async def vocab(self, vocab: VocabKind | None = None) -> list[VocabRecord]:
        async with self._store.transaction() as tx:
            return await tx.vocab(vocab)

    async def keys(self, item_ids: Sequence[uuid.UUID]) -> list[KeyRecord]:
        async with self._store.transaction() as tx:
            return await tx.keys(item_ids)

    async def write_log(self, turn_id: uuid.UUID) -> list[WriteLogRecord]:
        async with self._store.transaction() as tx:
            return await tx.write_log(turn_id)

    async def held_write(self, held_id: uuid.UUID) -> HeldWriteRecord | None:
        async with self._store.transaction() as tx:
            return await tx.get_held_write(held_id)

    async def held_writes(self, *, status: str | None = None) -> list[HeldWriteRecord]:
        async with self._store.transaction() as tx:
            return await tx.held_writes(status=status)

    async def entity_items(self, entity_id: uuid.UUID) -> list[uuid.UUID]:
        async with self._store.transaction() as tx:
            return await tx.entity_items(entity_id)

    async def expired_quick(self, now: datetime) -> list[ItemRecord]:
        async with self._store.transaction() as tx:
            return await tx.expired_quick(now)

    async def passed_triggers(self, now: datetime) -> list[TriggerRecord]:
        async with self._store.transaction() as tx:
            return await tx.passed_triggers(now)

    async def core(self) -> CoreView:
        """Core memory rendered within the token budget (S2.9)."""
        async with self._store.transaction() as tx:
            items = await tx.find_items(in_core=True, current_only=True, limit=500)
            entities = {e.id: e for e in await tx.list_entities()}
            rows = await tx.item_entities([i.id for i in items])
            intentions = await tx.find_items(
                kinds=[Kind.INTENTION], states=["wanted", "active"], limit=500
            )
        links: dict[uuid.UUID, list[uuid.UUID]] = {}
        for row in rows:
            links.setdefault(row.item_id, []).append(row.entity_id)
        return render_core(
            items,
            entities=entities,
            item_entities=links,
            open_intentions=intentions,
            budget=self._settings.core_token_budget,
        )


class Memory:
    """Memory for every workspace, given a store factory (Postgres, or in-memory in tests)."""

    def __init__(self, stores: MemoryStoreFactory, settings: MemorySettings | None = None) -> None:
        self._stores = stores
        self.settings = settings or MemorySettings()

    def reader(self, scope: WorkspaceScope) -> MemoryReader:
        return MemoryReader(self._stores(scope), self.settings)

    def writer(
        self,
        scope: WorkspaceScope,
        turn: WriterTurn,
        *,
        emit: Emit | None = None,
        confirmed: bool = False,
        undo_of: uuid.UUID | None = None,
    ) -> MemoryWriter:
        return MemoryWriter(
            self._stores(scope),
            turn,
            settings=WriterSettings(
                bulk_threshold=self.settings.bulk_threshold,
                core_token_budget=self.settings.core_token_budget,
            ),
            emit=emit,
            confirmed=confirmed,
            undo_of=undo_of,
        )

    def keys(
        self, scope: WorkspaceScope, *, timezone: str, embed: Embedder | None, model: str
    ) -> KeyIndexer:
        return KeyIndexer(
            self._stores(scope),
            timezone=timezone,
            embed=embed,
            model=model,
            verbal_enabled=self.settings.verbal_keys_enabled,
        )

    async def undo(
        self,
        scope: WorkspaceScope,
        turn: WriterTurn,
        undone_turn_id: uuid.UUID,
        *,
        emit: Emit | None = None,
    ) -> CommitResult:
        """Revert everything ``undone_turn_id`` wrote, as ``turn`` (FR-10.1)."""
        async with self._stores(scope).transaction() as tx:
            plan = await plan_undo(tx, undone_turn_id)
        writer = self.writer(scope, turn, emit=emit, undo_of=undone_turn_id)
        writer.add(*plan.ops)
        for title, reason, item_id in plan.conflicts:
            writer.note_conflict(title, reason, item_id=item_id)
        if plan.nothing_to_undo:
            writer.note_not_written("nothing to undo", "that turn made no memory changes")
        return await writer.commit()

    async def expiry_ops(self, scope: WorkspaceScope, now: datetime) -> list[Op]:
        """Quick-layer housekeeping due at ``now`` (S2.9), for a system turn to apply. A quick
        entry whose time is up leaves the layer, or stays under the next rule that still
        applies; a pending time trigger whose time passed becomes ``expired``."""
        reader = self.reader(scope)
        passed = await reader.passed_triggers(now)
        expired = await reader.expired_quick(now)
        owners = {i.id: i for i in await reader.items(sorted({t.item_id for t in passed}))}
        triggers = await reader.triggers([i.id for i in expired])
        ops: list[Op] = [
            SetTriggerState(
                trigger_id=t.id,
                state=TriggerState.EXPIRED,
                origin="system",
                title=f"reminder: {owners[t.item_id].title}" if t.item_id in owners else "reminder",
                rationale="its time passed",
            )
            for t in passed
        ]
        for item in expired:
            decision = quick_layer(
                item,
                now=now,
                triggers=[t for t in triggers if t.item_id == item.id],
                horizon_days=self.settings.quick_horizon_days,
                recent_days=self.settings.quick_recent_days,
            )
            ops.append(
                UpdateItem(
                    item_id=item.id,
                    changes={
                        "in_quick": decision.in_quick,
                        "quick_reason": decision.reason,
                        "quick_until": decision.until,
                    },
                    origin="system",
                    title=item.title,
                    rationale=(
                        f"quick: now {decision.reason}" if decision.in_quick else "quick time is up"
                    ),
                )
            )
        return ops

    async def confirm_held(
        self,
        scope: WorkspaceScope,
        turn: WriterTurn,
        held_id: uuid.UUID,
        *,
        emit: Emit | None = None,
    ) -> CommitResult:
        """Apply a held write as ``turn`` (a confirmation turn, so undo still works)."""
        held = await self._pending(scope, held_id)
        ops: list[Op] = [
            op.model_copy(update={"origin": "confirm"}) for op in map(load_op, held.ops)
        ]
        async with self._stores(scope).transaction() as tx:
            for op in ops:
                if not isinstance(op, CreateItem) and hasattr(op, "item_id"):
                    target = await tx.get_item(op.item_id)
                    if target is None or target.status is ItemStatus.DELETED:
                        raise ValidationFailedError("the memory this change was for is gone")
        writer = self.writer(scope, turn, emit=emit, confirmed=True)
        writer.add(*ops)
        result = await writer.commit()
        async with self._stores(scope).transaction() as tx:
            await tx.resolve_held_write(
                held_id, status="confirmed", turn_id=turn.turn_id, at=turn.now
            )
        return result

    async def reject_held(
        self, scope: WorkspaceScope, held_id: uuid.UUID, *, at: datetime
    ) -> HeldWriteRecord:
        await self._pending(scope, held_id)
        async with self._stores(scope).transaction() as tx:
            await tx.resolve_held_write(held_id, status="rejected", turn_id=None, at=at)
            held = await tx.get_held_write(held_id)
        if held is None:
            raise NotFoundError("held write not found")
        return held

    async def _pending(self, scope: WorkspaceScope, held_id: uuid.UUID) -> HeldWriteRecord:
        async with self._stores(scope).transaction() as tx:
            held = await tx.get_held_write(held_id)
        if held is None:
            raise NotFoundError("held write not found")
        if held.status != "pending":
            raise ValidationFailedError(f"this held write was already {held.status}")
        return held

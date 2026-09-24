"""The memory writer: the only way domain code changes memory (S2.2).

A writer is opened for one turn. Ops are added, then ``commit()`` applies them in **one**
transaction: each op is checked by the write policy before it is applied; allowed ops change
rows and write before/after snapshots to the write log (plus an item version); held ops wait in
``held_writes``; blocked ops leave a log row without their content. If anything fails, the
transaction rolls back and nothing persists (NFR-6.1).

After the commit the writer emits, in order, a ``policy`` and a ``tool_call`` event per op and
one ``memory_diff`` event built from the logged rows, so the diff can't disagree with what
happened. Keys are not written here: they are derived data, rebuilt after the commit.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from secondmind.core import (
    DiffEntry,
    EntityKind,
    ItemStatus,
    Layer,
    LinkType,
    MemoryDiffEvent,
    PolicyDecision,
    PolicyEvent,
    PolicyVerdict,
    ReconcileInfo,
    Sensitivity,
    TargetType,
    ToolCallEvent,
    TurnEvent,
    VocabKind,
    WriteOp,
    new_id,
    valid_state,
)
from secondmind.memory.core_layer import core_tokens_with
from secondmind.memory.diff import diff_entries
from secondmind.memory.ops import (
    AttachEntity,
    CreateItem,
    DeleteEntity,
    DeleteItem,
    DetachEntity,
    FulfilIntention,
    LinkItems,
    Op,
    RelateEntities,
    RestoreItem,
    SetItemState,
    SetTriggerState,
    SupersedeItem,
    UnlinkItems,
    UnrelateEntities,
    UpdateItem,
    UpdateTrigger,
    UpsertEntity,
    dump_op,
    op_label,
)
from secondmind.memory.records import (
    ITEM_FIELDS,
    CategoryRecord,
    EntityRecord,
    HeldWriteRecord,
    ItemEntityRecord,
    ItemRecord,
    LinkRecord,
    RelationRecord,
    TriggerRecord,
    VersionRecord,
    VocabRecord,
    WriteLogRecord,
)
from secondmind.memory.store import MemoryStore, MemoryTx
from secondmind.policy import OpFacts, PolicyContext, evaluate

Emit = Callable[[TurnEvent], Awaitable[None]]

CORE_BUDGET_RULE = "CORE-BUDGET"
_ITEM_EDIT_TYPES = (UpdateItem, SetItemState, DeleteItem, RestoreItem)


@dataclass(frozen=True, slots=True)
class WriterTurn:
    """The turn a writer belongs to. ``now`` is the turn's captured UTC instant."""

    turn_id: uuid.UUID
    workspace_id: uuid.UUID
    kind: str
    now: datetime


@dataclass(frozen=True, slots=True)
class WriterSettings:
    bulk_threshold: int = 5
    core_token_budget: int = 1_500


@dataclass(frozen=True, slots=True)
class OpOutcome:
    """What happened to one op: its verdict, the rows it logged and the diff entries."""

    op: Op
    verdict: PolicyVerdict
    rows: list[WriteLogRecord]
    entries: list[DiffEntry]
    result: str
    held_id: uuid.UUID | None = None


@dataclass(slots=True)
class CommitResult:
    outcomes: list[OpOutcome] = field(default_factory=list)
    notes: list[DiffEntry] = field(default_factory=list)
    diff: MemoryDiffEvent = field(default_factory=MemoryDiffEvent)
    touched_items: set[uuid.UUID] = field(default_factory=set)
    created_items: set[uuid.UUID] = field(default_factory=set)
    changed_entities: set[uuid.UUID] = field(default_factory=set)
    renamed_entities: set[uuid.UUID] = field(default_factory=set)
    held: list[HeldWriteRecord] = field(default_factory=list)

    @property
    def rows(self) -> list[WriteLogRecord]:
        return [r for o in self.outcomes for r in o.rows]

    def items_after(self) -> dict[uuid.UUID, dict[str, Any]]:
        """Latest committed snapshot of every item this turn changed."""
        out: dict[uuid.UUID, dict[str, Any]] = {}
        for row in self.rows:
            if row.target_type is TargetType.ITEM and row.after is not None:
                out[row.target_id] = row.after
        return out


class _Skip(Exception):  # noqa: N818 - control flow, not an error
    """An op that can't apply any more (its target is gone or already changed)."""


@dataclass(slots=True)
class _Applied:
    rows: list[WriteLogRecord]
    result: str


class _RowLog:
    """Collects the write-log rows (before/after snapshots) one applied op produces."""

    _ALLOWED = PolicyVerdict(decision=PolicyDecision.ALLOWED, rule_id="P-DEFAULT", reason="")

    def __init__(self, writer: "MemoryWriter", op: Op, seq: int) -> None:
        self._writer = writer
        self._op = op
        self._seq = seq
        self.rows: list[WriteLogRecord] = []

    def add(
        self,
        target: tuple[TargetType, uuid.UUID],
        before: BaseModel | None,
        after: BaseModel | None,
        layer: Layer,
        label: WriteOp | None = None,
    ) -> None:
        self.rows.append(
            self._writer.log_row(
                self._op,
                self._seq + len(self.rows) + 1,
                self._ALLOWED,
                target=target,
                layer=layer,
                before=_dump(before),
                after=_dump(after),
                label=label,
            )
        )

    def done(self, result: str) -> _Applied:
        return _Applied(self.rows, result)


class MemoryWriter:
    def __init__(
        self,
        store: MemoryStore,
        turn: WriterTurn,
        *,
        settings: WriterSettings | None = None,
        emit: Emit | None = None,
        confirmed: bool = False,
        undo_of: uuid.UUID | None = None,
    ) -> None:
        self._store = store
        self._turn = turn
        self._settings = settings or WriterSettings()
        self._emit = emit
        self._confirmed = confirmed
        self._undo_of = undo_of
        self._ops: list[Op] = []
        self._notes: list[DiffEntry] = []
        self._vocab: list[tuple[VocabKind, str, list[str]]] = []
        self._committed = False

    @property
    def turn(self) -> WriterTurn:
        return self._turn

    def add(self, *ops: Op) -> None:
        self._ops.extend(ops)

    @property
    def ops(self) -> list[Op]:
        return list(self._ops)

    def note_not_written(
        self,
        title: str,
        reason: str,
        *,
        rule_id: str | None = None,
        layer: Layer = Layer.ARCHIVE,
        reconcile: ReconcileInfo | None = None,
        item_id: uuid.UUID | None = None,
    ) -> None:
        """A deliberate non-write (FR-3.8), shown in the diff as ``∅ not written``."""
        self._notes.append(
            DiffEntry(
                op="not_written",
                layer=layer,
                title=title,
                reason=reason,
                rule_id=rule_id,
                reconcile=reconcile,
                item_id=item_id,
            )
        )

    def note_conflict(self, title: str, reason: str, *, item_id: uuid.UUID | None = None) -> None:
        self._notes.append(
            DiffEntry(
                op="conflict", layer=Layer.ARCHIVE, title=title, reason=reason, item_id=item_id
            )
        )

    def register_vocab(self, vocab: VocabKind, slug: str, aliases: Sequence[str] = ()) -> None:
        """Record a normalised slug; stored with the commit (not a logged op)."""
        self._vocab.append((vocab, slug, [a for a in aliases if a and a != slug]))

    async def commit(self) -> CommitResult:
        if self._committed:
            raise RuntimeError("a memory writer commits once")
        self._committed = True
        result = CommitResult()
        async with self._store.transaction() as tx:
            for vocab, slug, aliases in self._vocab:
                await tx.upsert_vocab(
                    VocabRecord(
                        id=new_id(),
                        workspace_id=self._turn.workspace_id,
                        vocab=vocab,
                        slug=slug,
                        aliases=aliases,
                    )
                )
            await self._apply_all(tx, result)
            rows = result.rows
            await tx.insert_write_log(rows)
            for held in result.held:
                await tx.insert_held_write(held)
            await self._write_versions(tx, rows)
        result.notes = list(self._notes)
        result.diff = MemoryDiffEvent(
            entries=[e for o in result.outcomes for e in o.entries] + result.notes,
            undo_of=self._undo_of,
        )
        await self._emit_events(result)
        return result

    # ------------------------------------------------------------------ applying

    async def _apply_all(self, tx: MemoryTx, result: CommitResult) -> None:
        created = {op.item_id for op in self._ops if isinstance(op, CreateItem)}
        edited = self._edited_items(created)
        context = PolicyContext(
            turn_kind=self._turn.kind,
            confirmed=self._confirmed,
            edited_items=len(edited),
            bulk_threshold=self._settings.bulk_threshold,
        )
        if not self._confirmed and len(edited) > self._settings.bulk_threshold:
            await self._hold_everything(result, context)
            return
        seq = 0
        for op in self._ops:
            facts = await self._facts(tx, op, created)
            verdict = evaluate(facts, context)
            if verdict.decision is PolicyDecision.ALLOWED and facts.core_write:
                verdict = await self._core_budget(tx, op, verdict)
            label = op_label(op)
            if verdict.decision is not PolicyDecision.ALLOWED:
                seq += 1
                result.outcomes.append(await self._refuse(tx, op, verdict, seq, result))
                continue
            try:
                applied = await self._apply(tx, op, seq)
            except _Skip as skip:
                reason = str(skip)
                note = PolicyVerdict(
                    decision=PolicyDecision.BLOCKED, rule_id="NOT-APPLICABLE", reason=reason
                )
                entry = DiffEntry(
                    op="not_written", layer=Layer.ARCHIVE, title=op.title, reason=reason
                )
                result.outcomes.append(OpOutcome(op, note, [], [entry], f"skipped: {reason}"))
                continue
            seq += len(applied.rows)
            self._track(result, applied.rows)
            result.outcomes.append(
                OpOutcome(
                    op=op,
                    verdict=verdict,
                    rows=applied.rows,
                    entries=diff_entries(op, label, applied.rows),
                    result=applied.result,
                )
            )

    def _edited_items(self, created: set[uuid.UUID]) -> set[uuid.UUID]:
        edited: set[uuid.UUID] = set()
        for op in self._ops:
            if isinstance(op, _ITEM_EDIT_TYPES):
                edited.add(op.item_id)
            elif isinstance(op, SupersedeItem):
                edited.add(op.old_id)
            elif isinstance(op, FulfilIntention):
                edited.add(op.intention_id)
        return edited - created

    async def _hold_everything(self, result: CommitResult, context: PolicyContext) -> None:
        verdict = PolicyVerdict(
            decision=PolicyDecision.HELD,
            rule_id="P-BULK-1",
            reason=(
                f"changes {context.edited_items} items (more than {context.bulk_threshold}) "
                "in one turn"
            ),
        )
        held_id = new_id()
        title = f"{len(self._ops)} changes to {context.edited_items} items"
        result.held.append(
            HeldWriteRecord(
                id=held_id,
                workspace_id=self._turn.workspace_id,
                turn_id=self._turn.turn_id,
                ops=[dump_op(op) for op in self._ops],
                rule_id=verdict.rule_id,
                reason=verdict.reason,
                title=title,
                layer=Layer.ARCHIVE,
                created_at=self._turn.now,
            )
        )
        for seq, op in enumerate(self._ops, start=1):
            row = self.log_row(op, seq, verdict, target=_target(op), layer=Layer.ARCHIVE)
            result.outcomes.append(OpOutcome(op, verdict, [row], [], "held", held_id))
        result.outcomes[0].entries.append(
            DiffEntry(
                op="held",
                layer=Layer.ARCHIVE,
                title=title,
                reason=verdict.reason,
                rule_id=verdict.rule_id,
                held_write_id=held_id,
            )
        )

    async def _refuse(
        self, tx: MemoryTx, op: Op, verdict: PolicyVerdict, seq: int, result: CommitResult
    ) -> OpOutcome:
        target_type, target_id = _target(op)
        layer = await self._proposed_layer(tx, op)
        secret = isinstance(op, CreateItem) and op.item.sensitivity is Sensitivity.SECRET
        title = "a secret (not shown)" if secret else op.title
        row = self.log_row(op, seq, verdict, target=(target_type, target_id), layer=layer)
        if verdict.decision is PolicyDecision.HELD:
            held_id = new_id()
            result.held.append(
                HeldWriteRecord(
                    id=held_id,
                    workspace_id=self._turn.workspace_id,
                    turn_id=self._turn.turn_id,
                    ops=[dump_op(op)],
                    rule_id=verdict.rule_id,
                    reason=verdict.reason,
                    title=title,
                    layer=layer,
                    created_at=self._turn.now,
                )
            )
            entry = DiffEntry(
                op="held",
                layer=layer,
                item_id=target_id if target_type is TargetType.ITEM else None,
                title=title,
                reason=verdict.reason,
                rule_id=verdict.rule_id,
                held_write_id=held_id,
                reconcile=op.reconcile,
            )
            return OpOutcome(op, verdict, [row], [entry], "held for confirmation", held_id)
        entry = DiffEntry(
            op="not_written",
            layer=layer,
            title=title,
            reason=verdict.reason,
            rule_id=verdict.rule_id,
            reconcile=op.reconcile,
        )
        return OpOutcome(op, verdict, [row], [entry], "blocked, nothing written")

    async def _proposed_layer(self, tx: MemoryTx, op: Op) -> Layer:
        if isinstance(op, CreateItem):
            if op.item.in_core:
                return Layer.CORE
            return Layer.QUICK if op.item.in_quick else Layer.ARCHIVE
        if isinstance(op, UpdateItem) and op.changes.get("in_core"):
            return Layer.CORE
        target_type, target_id = _target(op)
        if target_type is TargetType.ITEM:
            item = await tx.get_item(target_id)
            if item is not None:
                return item.layer
        return Layer.ARCHIVE

    async def _core_budget(self, tx: MemoryTx, op: Op, verdict: PolicyVerdict) -> PolicyVerdict:
        _, target_id = _target(op)
        candidate = await tx.get_item(target_id)
        if isinstance(op, CreateItem):
            candidate = self._new_item(op, category_id=None)
        if candidate is None:
            return verdict
        current = [i for i in await tx.find_items(in_core=True) if i.id != candidate.id]
        tokens = core_tokens_with(current, candidate)
        if tokens > self._settings.core_token_budget:
            return PolicyVerdict(
                decision=PolicyDecision.BLOCKED,
                rule_id=CORE_BUDGET_RULE,
                reason=(
                    f"core budget full ({tokens} > {self._settings.core_token_budget} tokens); "
                    "kept in the archive"
                ),
            )
        return verdict

    async def _facts(self, tx: MemoryTx, op: Op, created: set[uuid.UUID]) -> OpFacts:
        label = op_label(op)
        base: dict[str, Any] = {"op": label, "trust": op.trust, "origin": op.origin}
        if isinstance(op, CreateItem):
            item = op.item
            return OpFacts(
                **base,
                kind=item.kind,
                sensitivity=item.sensitivity,
                modality=item.modality,
                confidence=item.confidence,
                core_write=item.in_core,
                creates_trigger=bool(op.triggers),
            )
        target_type, target_id = _target(op)
        if target_type is TargetType.ITEM:
            item_id = target_id
            before = await tx.get_item(item_id)
            if before is None:
                return OpFacts(**base)
            changes = self._item_changes(op, before)
            after_core = bool(changes.get("in_core", before.in_core))
            content_changed = any(
                k not in ("in_quick", "quick_reason", "quick_until") for k in changes
            )
            core_write = after_core and (not before.in_core or content_changed)
            if isinstance(op, DeleteItem):
                core_write = False
            sensitivity = changes.get("sensitivity", before.sensitivity)
            return OpFacts(
                **base,
                kind=before.kind,
                sensitivity=Sensitivity(sensitivity),
                modality=before.modality,
                confidence=before.confidence,
                core_write=core_write,
                edits_existing=item_id not in created,
            )
        if target_type is TargetType.TRIGGER:
            return OpFacts(**base, target="trigger", edits_existing=True)
        if target_type is TargetType.ENTITY:
            return OpFacts(**base, target="entity", edits_existing=not getattr(op, "create", False))
        return OpFacts(**base, target=target_type.value)

    @staticmethod
    def _item_changes(op: Op, before: ItemRecord) -> dict[str, Any]:  # noqa: PLR0911
        if isinstance(op, UpdateItem):
            return dict(op.changes)
        if isinstance(op, SetItemState):
            return {"state": op.state}
        if isinstance(op, SupersedeItem):
            return {"state": op.state, "valid_to": op.valid_to}
        if isinstance(op, FulfilIntention):
            return {"state": op.state}
        if isinstance(op, DeleteItem):
            return {"status": ItemStatus.DELETED}
        if isinstance(op, RestoreItem):
            return {"status": ItemStatus.ACTIVE}
        return {}

    async def _apply(self, tx: MemoryTx, op: Op, seq: int) -> _Applied:  # noqa: PLR0911, PLR0912
        log = _RowLog(self, op, seq)
        match op:
            case CreateItem():
                return await self._create_item(tx, op, log)
            case UpdateItem() | SetItemState() | DeleteItem() | RestoreItem():
                before = await self._live_item(tx, op.item_id, allow_deleted=True)
                after = self._changed_item(before, self._item_changes(op, before))
                await tx.replace_item(after)
                log.add((TargetType.ITEM, after.id), before, after, after.layer)
                return log.done(_describe_item_change(before, after))
            case SupersedeItem():
                return await self._supersede(tx, op, log)
            case FulfilIntention():
                return await self._fulfil(tx, op, log)
            case LinkItems():
                await self._live_item(tx, op.src_id)
                await self._live_item(tx, op.dst_id)
                link = await self._insert_link(tx, op.link_id, op.src_id, op.link_type, op.dst_id)
                log.add((TargetType.LINK, link.id), None, link, Layer.ARCHIVE)
                return log.done(f"linked ({op.link_type.value})")
            case UnlinkItems():
                old_link = await tx.get_link(op.link_id)
                if old_link is None:
                    raise _Skip("the link no longer exists")
                await tx.delete_link(op.link_id)
                log.add((TargetType.LINK, op.link_id), old_link, None, Layer.ARCHIVE)
                return log.done("unlinked")
            case AttachEntity():
                return await self._attach(tx, op, log)
            case DetachEntity():
                old_row = await tx.get_item_entity(op.row_id)
                if old_row is None:
                    raise _Skip("the entity link no longer exists")
                await tx.delete_item_entity(op.row_id)
                log.add((TargetType.ITEM_ENTITY, op.row_id), old_row, None, Layer.ARCHIVE)
                return log.done("detached entity")
            case UpsertEntity():
                return await self._upsert_entity(tx, op, log)
            case DeleteEntity():
                return await self._delete_entity(tx, op, log)
            case RelateEntities():
                return await self._relate(tx, op, log)
            case UnrelateEntities():
                old_relation = await tx.get_relation(op.relation_id)
                if old_relation is None:
                    raise _Skip("the relation no longer exists")
                await tx.delete_relation(op.relation_id)
                log.add((TargetType.RELATION, op.relation_id), old_relation, None, Layer.ARCHIVE)
                return log.done("unrelated")
            case SetTriggerState() | UpdateTrigger():
                return await self._change_trigger(tx, op, log)
        raise TypeError(f"unhandled op {type(op).__name__}")

    async def _create_item(self, tx: MemoryTx, op: CreateItem, log: "_RowLog") -> _Applied:
        category_id = await self._category(tx, op)
        record = self._new_item(op, category_id)
        if await tx.get_item(record.id) is not None:
            raise _Skip("already exists")
        await tx.insert_item(record)
        log.add((TargetType.ITEM, record.id), None, record, record.layer)
        for link in op.entities:
            if await tx.get_entity(link.entity_id) is None:
                raise ValueError(f"entity {link.entity_id} does not exist")
            row = ItemEntityRecord(
                id=link.row_id,
                workspace_id=self._turn.workspace_id,
                item_id=record.id,
                entity_id=link.entity_id,
                role=link.role,
                created_by_turn_id=self._turn.turn_id,
            )
            await tx.insert_item_entity(row)
            log.add((TargetType.ITEM_ENTITY, row.id), None, row, record.layer, WriteOp.LINK)
        for new in op.triggers:
            trigger = TriggerRecord(
                **new.trigger.model_dump(),
                id=new.trigger_id,
                workspace_id=self._turn.workspace_id,
                item_id=record.id,
                created_at=self._turn.now,
                updated_at=self._turn.now,
                created_by_turn_id=self._turn.turn_id,
                updated_by_turn_id=self._turn.turn_id,
            )
            await tx.insert_trigger(trigger)
            log.add((TargetType.TRIGGER, trigger.id), None, trigger, record.layer)
        return log.done(f"created {record.kind.value} in {record.layer.value}")

    async def _supersede(self, tx: MemoryTx, op: SupersedeItem, log: "_RowLog") -> _Applied:
        old = await self._live_item(tx, op.old_id)
        new = await self._live_item(tx, op.new_id)
        if old.valid_to is not None or old.state not in ("current", "scheduled"):
            raise _Skip("the old memory was already superseded")
        after = self._changed_item(old, {"state": op.state, "valid_to": op.valid_to})
        await tx.replace_item(after)
        log.add((TargetType.ITEM, old.id), old, after, old.layer)
        link = await self._insert_link(tx, op.link_id, new.id, LinkType.SUPERSEDES, old.id)
        log.add((TargetType.LINK, link.id), None, link, old.layer)
        return log.done(f"superseded {old.kind.value} ({op.state})")

    async def _fulfil(self, tx: MemoryTx, op: FulfilIntention, log: "_RowLog") -> _Applied:
        intention = await self._live_item(tx, op.intention_id)
        episode = await self._live_item(tx, op.episode_id)
        if intention.state in ("fulfilled", "dropped"):
            raise _Skip("the intention was already closed")
        after = self._changed_item(intention, {"state": op.state})
        await tx.replace_item(after)
        log.add((TargetType.ITEM, intention.id), intention, after, intention.layer)
        link = await self._insert_link(tx, op.link_id, episode.id, LinkType.FULFILS, intention.id)
        log.add((TargetType.LINK, link.id), None, link, intention.layer)
        return log.done("intention fulfilled")

    async def _attach(self, tx: MemoryTx, op: AttachEntity, log: "_RowLog") -> _Applied:
        await self._live_item(tx, op.item_id, allow_deleted=True)
        row = ItemEntityRecord(
            id=op.row_id,
            workspace_id=self._turn.workspace_id,
            item_id=op.item_id,
            entity_id=op.entity_id,
            role=op.role,
            created_by_turn_id=self._turn.turn_id,
        )
        await tx.insert_item_entity(row)
        log.add((TargetType.ITEM_ENTITY, row.id), None, row, Layer.ARCHIVE)
        return log.done(f"attached entity ({op.role.value})")

    async def _delete_entity(self, tx: MemoryTx, op: DeleteEntity, log: "_RowLog") -> _Applied:
        entity = await tx.get_entity(op.entity_id)
        if entity is None or entity.kind is EntityKind.SELF:
            raise _Skip("the entity can't be removed")
        after = entity.model_copy(
            update={
                "status": "deleted",
                "updated_at": self._turn.now,
                "updated_by_turn_id": self._turn.turn_id,
            }
        )
        await tx.replace_entity(after)
        log.add((TargetType.ENTITY, entity.id), entity, after, Layer.ARCHIVE)
        return log.done(f"removed {entity.kind.value}")

    async def _relate(self, tx: MemoryTx, op: RelateEntities, log: "_RowLog") -> _Applied:
        relation = RelationRecord(
            id=op.relation_id,
            workspace_id=self._turn.workspace_id,
            src_entity_id=op.src_entity_id,
            relation=op.relation,
            dst_entity_id=op.dst_entity_id,
            valid_from=op.valid_from,
            evidence_item_id=op.evidence_item_id,
            created_by_turn_id=self._turn.turn_id,
        )
        await tx.insert_relation(relation)
        log.add((TargetType.RELATION, relation.id), None, relation, Layer.ARCHIVE)
        return log.done(f"related ({op.relation})")

    async def _change_trigger(
        self, tx: MemoryTx, op: SetTriggerState | UpdateTrigger, log: "_RowLog"
    ) -> _Applied:
        trigger = await tx.get_trigger(op.trigger_id)
        if trigger is None:
            raise _Skip("the trigger no longer exists")
        changes = {"state": op.state} if isinstance(op, SetTriggerState) else dict(op.changes)
        after = TriggerRecord.model_validate(
            {
                **trigger.model_dump(),
                **changes,
                "updated_at": self._turn.now,
                "updated_by_turn_id": self._turn.turn_id,
            }
        )
        await tx.replace_trigger(after)
        log.add((TargetType.TRIGGER, trigger.id), trigger, after, Layer.QUICK)
        return log.done(f"trigger {after.state.value}")

    async def _upsert_entity(self, tx: MemoryTx, op: UpsertEntity, log: "_RowLog") -> _Applied:
        before = await tx.get_entity(op.entity_id)
        if op.create:
            if before is not None:
                raise _Skip("the entity already exists")
            record = EntityRecord(
                **op.entity.model_dump(),
                id=op.entity_id,
                workspace_id=self._turn.workspace_id,
                created_at=self._turn.now,
                updated_at=self._turn.now,
                created_by_turn_id=self._turn.turn_id,
                updated_by_turn_id=self._turn.turn_id,
            )
            await tx.insert_entity(record)
            log.add((TargetType.ENTITY, record.id), None, record, Layer.ARCHIVE)
            return log.done(f"new {record.kind.value} {record.name!r}")
        if before is None:
            raise _Skip("the entity no longer exists")
        after = EntityRecord.model_validate(
            {
                **before.model_dump(),
                **op.entity.model_dump(),
                "updated_at": self._turn.now,
                "updated_by_turn_id": self._turn.turn_id,
            }
        )
        await tx.replace_entity(after)
        log.add((TargetType.ENTITY, after.id), before, after, Layer.ARCHIVE)
        return log.done(f"updated {after.kind.value} {after.name!r}")

    async def _live_item(
        self, tx: MemoryTx, item_id: uuid.UUID, *, allow_deleted: bool = False
    ) -> ItemRecord:
        item = await tx.get_item(item_id)
        if item is None:
            raise _Skip("the memory no longer exists")
        if item.status is ItemStatus.DELETED and not allow_deleted:
            raise _Skip("the memory was deleted")
        return item

    async def _insert_link(
        self,
        tx: MemoryTx,
        link_id: uuid.UUID,
        src: uuid.UUID,
        link_type: LinkType,
        dst: uuid.UUID,
    ) -> LinkRecord:
        link = LinkRecord(
            id=link_id,
            workspace_id=self._turn.workspace_id,
            src_item_id=src,
            link_type=link_type,
            dst_item_id=dst,
            created_by_turn_id=self._turn.turn_id,
        )
        await tx.insert_link(link)
        return link

    async def _category(self, tx: MemoryTx, op: CreateItem) -> uuid.UUID | None:
        if not op.category_slug:
            return op.item.category_id
        stored = await tx.upsert_category(
            CategoryRecord(
                id=new_id(),
                workspace_id=self._turn.workspace_id,
                slug=op.category_slug,
                display_name=op.category_name or op.category_slug.rsplit("/", 1)[-1],
            )
        )
        return stored.id

    def _new_item(self, op: CreateItem, category_id: uuid.UUID | None) -> ItemRecord:
        content = op.item.model_dump()
        if category_id is not None:
            content["category_id"] = category_id
        if content["in_core"] and self._confirmed:
            content["core_confirmed_at"] = self._turn.now
        if not valid_state(op.item.kind, op.item.state):
            raise ValueError(f"state {op.item.state!r} is not valid for kind {op.item.kind}")
        return ItemRecord(
            **content,
            id=op.item_id,
            workspace_id=self._turn.workspace_id,
            created_at=self._turn.now,
            updated_at=self._turn.now,
            created_by_turn_id=self._turn.turn_id,
            updated_by_turn_id=self._turn.turn_id,
        )

    def _changed_item(self, before: ItemRecord, changes: dict[str, Any]) -> ItemRecord:
        unknown = set(changes) - ITEM_FIELDS
        if unknown:
            raise ValueError(f"unknown item fields: {sorted(unknown)}")
        data = {
            **before.model_dump(),
            **changes,
            "updated_at": self._turn.now,
            "updated_by_turn_id": self._turn.turn_id,
        }
        if changes.get("in_core") and not before.in_core and self._confirmed:
            data["core_confirmed_at"] = self._turn.now
        after = ItemRecord.model_validate(data)
        if not valid_state(after.kind, after.state):
            raise ValueError(f"state {after.state!r} is not valid for kind {after.kind}")
        return after

    def log_row(
        self,
        op: Op,
        seq: int,
        verdict: PolicyVerdict,
        *,
        target: tuple[TargetType, uuid.UUID],
        layer: Layer,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        label: WriteOp | None = None,
    ) -> WriteLogRecord:
        return WriteLogRecord(
            turn_id=self._turn.turn_id,
            seq=seq,
            op=label or op_label(op),
            target_type=target[0],
            target_id=target[1],
            layer=layer,
            title=op.title[:300],
            before=before,
            after=after,
            rationale=op.rationale[:1000],
            trust=op.trust,
            decision=verdict.decision,
            rule_id=verdict.rule_id,
            reason=verdict.reason,
        )

    def _track(self, result: CommitResult, rows: list[WriteLogRecord]) -> None:
        for row in rows:
            if row.target_type is TargetType.ITEM:
                result.touched_items.add(row.target_id)
                if row.before is None:
                    result.created_items.add(row.target_id)
            elif row.target_type is TargetType.ENTITY:
                result.changed_entities.add(row.target_id)
                if row.before is not None and row.after is not None:
                    renamed = row.before.get("name") != row.after.get("name") or row.before.get(
                        "labels"
                    ) != row.after.get("labels")
                    if renamed:
                        result.renamed_entities.add(row.target_id)
            elif row.target_type is TargetType.LINK:
                snapshot = row.after or row.before or {}
                for key in ("src_item_id", "dst_item_id"):
                    if key in snapshot:
                        result.touched_items.add(uuid.UUID(str(snapshot[key])))

    async def _write_versions(self, tx: MemoryTx, rows: list[WriteLogRecord]) -> None:
        for row in rows:
            if (
                row.target_type is TargetType.ITEM
                and row.after is not None
                and row.decision is PolicyDecision.ALLOWED
            ):
                await tx.insert_version(
                    VersionRecord(
                        item_id=row.target_id,
                        turn_id=self._turn.turn_id,
                        version=await tx.next_version(row.target_id),
                        snapshot=row.after,
                    )
                )

    async def _emit_events(self, result: CommitResult) -> None:
        if self._emit is None:
            return
        for outcome in result.outcomes:
            label = op_label(outcome.op).value
            target = outcome.entries[0].title if outcome.entries else outcome.op.title
            await self._emit(PolicyEvent(op=label, target=target or None, verdict=outcome.verdict))
            await self._emit(
                ToolCallEvent(
                    tool=f"memory.{label}",
                    arguments=_summarise_op(outcome.op),
                    result_summary=outcome.result,
                    policy=outcome.verdict,
                )
            )
        await self._emit(result.diff)


def _target(op: Op) -> tuple[TargetType, uuid.UUID]:  # noqa: PLR0911
    match op:
        case CreateItem():
            return TargetType.ITEM, op.item_id
        case UpdateItem() | SetItemState() | DeleteItem() | RestoreItem():
            return TargetType.ITEM, op.item_id
        case SupersedeItem():
            return TargetType.ITEM, op.old_id
        case FulfilIntention():
            return TargetType.ITEM, op.intention_id
        case LinkItems() | UnlinkItems():
            return TargetType.LINK, op.link_id
        case AttachEntity() | DetachEntity():
            return TargetType.ITEM_ENTITY, op.row_id
        case UpsertEntity() | DeleteEntity():
            return TargetType.ENTITY, op.entity_id
        case RelateEntities() | UnrelateEntities():
            return TargetType.RELATION, op.relation_id
        case SetTriggerState() | UpdateTrigger():
            return TargetType.TRIGGER, op.trigger_id
    raise TypeError(f"unhandled op {type(op).__name__}")


def _dump(value: BaseModel | None) -> dict[str, Any] | None:
    return None if value is None else value.model_dump(mode="json")


def _describe_item_change(before: ItemRecord, after: ItemRecord) -> str:
    if before.status is not after.status:
        return f"{after.kind.value} {after.status.value}"
    if before.state != after.state:
        return f"{after.kind.value} {before.state} → {after.state}"
    if after.in_core and not before.in_core:
        return f"{after.kind.value} added to core"
    return f"{after.kind.value} updated"


def _summarise_op(op: Op) -> dict[str, str]:
    """Tool-call arguments for the glass box: short, and never the content of a secret."""
    args: dict[str, str] = {}
    if isinstance(op, CreateItem):
        item = op.item
        if item.sensitivity is Sensitivity.SECRET:
            return {"kind": item.kind.value, "content": "[redacted]"}
        args = {"kind": item.kind.value, "title": _short(item.title), "state": item.state}
        if item.subtype:
            args["subtype"] = item.subtype
        if op.entities:
            args["entities"] = str(len(op.entities))
        if op.triggers:
            args["triggers"] = str(len(op.triggers))
        return args
    data = op.model_dump(mode="json", exclude={"rationale", "trust", "origin", "reconcile", "type"})
    for name, value in data.items():
        if value in (None, "", [], {}):
            continue
        args[name] = _short(value if isinstance(value, str) else str(value))
    return args


def _short(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"

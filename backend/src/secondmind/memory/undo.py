"""Undo a turn (S2.12, FR-10.1): replay its write log backwards as a new turn.

Created rows are soft-deleted (items, entities) or removed (links, relations), updated rows go
back to their before snapshot, a supersede reopens the old row (``valid_to`` -> NULL) and a
fulfil restores the intention's state. The reversals are ordinary ops, so they go through the
policy and the write log, and an undo can itself be undone (redo).

**Conflicts:** a row a later turn changed is not overwritten. It is reported as a conflict in
the undo's diff and everything else is undone.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from secondmind.core import (
    EntityKind,
    ItemStatus,
    PolicyDecision,
    TargetType,
    TriggerState,
    new_id,
)
from secondmind.memory.ops import (
    AttachEntity,
    DeleteEntity,
    DeleteItem,
    DetachEntity,
    LinkItems,
    Op,
    RelateEntities,
    RestoreItem,
    SetTriggerState,
    UnlinkItems,
    UnrelateEntities,
    UpdateItem,
    UpdateTrigger,
    UpsertEntity,
)
from secondmind.memory.records import (
    ITEM_FIELDS,
    EntityContent,
    ItemEntityRecord,
    LinkRecord,
    RelationRecord,
    TriggerContent,
    WriteLogRecord,
)
from secondmind.memory.store import MemoryTx

_TRIGGER_FIELDS = frozenset(TriggerContent.model_fields)


@dataclass(slots=True)
class UndoPlan:
    ops: list[Op] = field(default_factory=list)
    conflicts: list[tuple[str, str, uuid.UUID | None]] = field(default_factory=list)
    nothing_to_undo: bool = False


async def plan_undo(tx: MemoryTx, turn_id: uuid.UUID) -> UndoPlan:
    """Inverse ops for everything ``turn_id`` wrote, newest first."""
    rows = [r for r in await tx.write_log(turn_id) if r.decision is PolicyDecision.ALLOWED]
    plan = UndoPlan(nothing_to_undo=not rows)
    conflicted: set[uuid.UUID] = set()
    for target_type, target_id in dict.fromkeys((r.target_type, r.target_id) for r in rows):
        reason = await _conflict(tx, target_type, target_id, turn_id)
        if reason:
            conflicted.add(target_id)
            title = next(r.title for r in rows if r.target_id == target_id)
            item_id = target_id if target_type is TargetType.ITEM else None
            plan.conflicts.append((title or target_type.value, reason, item_id))
    for row in sorted(rows, key=lambda r: r.seq, reverse=True):
        if row.target_id in conflicted:
            continue
        op = _inverse(row)
        if op is not None:
            plan.ops.append(op)
    return plan


async def _conflict(
    tx: MemoryTx, target_type: TargetType, target_id: uuid.UUID, turn_id: uuid.UUID
) -> str | None:
    if target_type is TargetType.ITEM:
        item = await tx.get_item(target_id)
        if item is not None and item.updated_by_turn_id != turn_id:
            return "a later turn changed it, so it was left as it is"
    elif target_type is TargetType.ENTITY:
        entity = await tx.get_entity(target_id)
        if entity is not None and entity.updated_by_turn_id != turn_id:
            return "a later turn changed it, so it was left as it is"
        if entity is not None and entity.created_by_turn_id == turn_id:
            others = await tx.entity_items(target_id)
            created_here = {
                i.id for i in await tx.get_items(others) if i.created_by_turn_id == turn_id
            }
            if set(others) - created_here:
                return "other memories still point at it, so it was kept"
    elif target_type is TargetType.TRIGGER:
        trigger = await tx.get_trigger(target_id)
        if trigger is not None and trigger.updated_by_turn_id != turn_id:
            return "a later turn changed it, so it was left as it is"
    return None


def _inverse(row: WriteLogRecord) -> Op | None:  # noqa: PLR0911, PLR0912
    common: dict[str, Any] = {"title": row.title, "origin": "undo", "rationale": "undo"}
    before, after = row.before, row.after
    match row.target_type:
        case TargetType.ITEM:
            if before is None:
                return DeleteItem(item_id=row.target_id, **common)
            if after is None:
                return None
            if (
                before.get("status") == ItemStatus.ACTIVE
                and after.get("status") == ItemStatus.DELETED
            ):
                return RestoreItem(item_id=row.target_id, **common)
            if (
                before.get("status") == ItemStatus.DELETED
                and after.get("status") == ItemStatus.ACTIVE
            ):
                return DeleteItem(item_id=row.target_id, **common)
            changes = {
                k: before.get(k)
                for k in ITEM_FIELDS
                if before.get(k) != after.get(k) and k not in ("access_count", "last_accessed_at")
            }
            return UpdateItem(item_id=row.target_id, changes=changes, **common) if changes else None
        case TargetType.ENTITY:
            if before is None:
                if (after or {}).get("kind") == EntityKind.SELF:
                    return None
                return DeleteEntity(entity_id=row.target_id, **common)
            content = EntityContent.model_validate(
                {k: v for k, v in before.items() if k in EntityContent.model_fields}
            )
            return UpsertEntity(entity_id=row.target_id, entity=content, create=False, **common)
        case TargetType.LINK:
            if before is None:
                return UnlinkItems(link_id=row.target_id, **common)
            link = LinkRecord.model_validate(before)
            return LinkItems(
                link_id=new_id(),
                src_id=link.src_item_id,
                link_type=link.link_type,
                dst_id=link.dst_item_id,
                **common,
            )
        case TargetType.ITEM_ENTITY:
            if before is None:
                return DetachEntity(row_id=row.target_id, **common)
            ie = ItemEntityRecord.model_validate(before)
            return AttachEntity(
                row_id=new_id(), item_id=ie.item_id, entity_id=ie.entity_id, role=ie.role, **common
            )
        case TargetType.RELATION:
            if before is None:
                return UnrelateEntities(relation_id=row.target_id, **common)
            rel = RelationRecord.model_validate(before)
            return RelateEntities(
                relation_id=new_id(),
                src_entity_id=rel.src_entity_id,
                relation=rel.relation,
                dst_entity_id=rel.dst_entity_id,
                valid_from=rel.valid_from,
                evidence_item_id=rel.evidence_item_id,
                **common,
            )
        case TargetType.TRIGGER:
            if before is None:
                return SetTriggerState(
                    trigger_id=row.target_id, state=TriggerState.CANCELLED, **common
                )
            changes = {
                k: before.get(k)
                for k in _TRIGGER_FIELDS
                if after is not None and before.get(k) != after.get(k)
            }
            return (
                UpdateTrigger(trigger_id=row.target_id, changes=changes, **common)
                if changes
                else None
            )

"""Operations the memory writer accepts. Each is checked by the write policy before it is applied,
and becomes one or more write-log rows. Ops are JSON round-trippable, so a held op can wait in
``held_writes`` and be applied later by a confirmation turn."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from secondmind.core import (
    EntityRole,
    LinkType,
    ReconcileInfo,
    TriggerState,
    Trust,
    WriteOp,
)
from secondmind.memory.records import EntityContent, ItemContent, TriggerContent

# Where an op came from. "user_message", "ui_edit" (the glass box, Upcoming), "undo" and
# "confirm" are the user's own action.
Origin = Literal["user_message", "ui_edit", "content", "system", "undo", "confirm"]


class _Op(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = ""
    rationale: str = ""
    trust: Trust = Trust.USER_STATED
    origin: Origin = "user_message"
    reconcile: ReconcileInfo | None = None


class EntityLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    row_id: uuid.UUID
    entity_id: uuid.UUID
    role: EntityRole


class NewTrigger(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger_id: uuid.UUID
    trigger: TriggerContent


class CreateItem(_Op):
    """A new item with its entity roles and triggers. ``category_slug`` is resolved (or created)
    by the writer inside the commit, so the item's ``category_id`` is set there."""

    type: Literal["create_item"] = "create_item"
    item_id: uuid.UUID
    item: ItemContent
    entities: list[EntityLink] = []
    triggers: list[NewTrigger] = []
    category_slug: str | None = None
    category_name: str | None = None


class UpdateItem(_Op):
    type: Literal["update_item"] = "update_item"
    item_id: uuid.UUID
    changes: dict[str, Any]


class SetItemState(_Op):
    type: Literal["set_item_state"] = "set_item_state"
    item_id: uuid.UUID
    state: str


class SupersedeItem(_Op):
    """The old row stays as history: its ``valid_to`` is set and it gets ``state`` (superseded,
    or moved for a plan). The new row links to it with ``supersedes``."""

    type: Literal["supersede_item"] = "supersede_item"
    old_id: uuid.UUID
    new_id: uuid.UUID
    link_id: uuid.UUID
    valid_to: datetime
    state: str = "superseded"


class CorrectItem(_Op):
    """The old row was recorded by mistake (S3.12): it is archived (kept for the record, out of
    recall) and the new row links to it with ``corrects``. Not history: that is a supersede."""

    type: Literal["correct_item"] = "correct_item"
    old_id: uuid.UUID
    new_id: uuid.UUID
    link_id: uuid.UUID


class FulfilIntention(_Op):
    type: Literal["fulfil_intention"] = "fulfil_intention"
    intention_id: uuid.UUID
    episode_id: uuid.UUID
    link_id: uuid.UUID
    state: str = "fulfilled"


class DeleteItem(_Op):
    """Soft delete: the row stays with ``status = deleted``."""

    type: Literal["delete_item"] = "delete_item"
    item_id: uuid.UUID


class RestoreItem(_Op):
    type: Literal["restore_item"] = "restore_item"
    item_id: uuid.UUID


class LinkItems(_Op):
    type: Literal["link_items"] = "link_items"
    link_id: uuid.UUID
    src_id: uuid.UUID
    link_type: LinkType
    dst_id: uuid.UUID


class UnlinkItems(_Op):
    type: Literal["unlink_items"] = "unlink_items"
    link_id: uuid.UUID


class AttachEntity(_Op):
    type: Literal["attach_entity"] = "attach_entity"
    row_id: uuid.UUID
    item_id: uuid.UUID
    entity_id: uuid.UUID
    role: EntityRole


class DetachEntity(_Op):
    type: Literal["detach_entity"] = "detach_entity"
    row_id: uuid.UUID


class UpsertEntity(_Op):
    type: Literal["upsert_entity"] = "upsert_entity"
    entity_id: uuid.UUID
    entity: EntityContent
    create: bool


class DeleteEntity(_Op):
    type: Literal["delete_entity"] = "delete_entity"
    entity_id: uuid.UUID


class RelateEntities(_Op):
    type: Literal["relate_entities"] = "relate_entities"
    relation_id: uuid.UUID
    src_entity_id: uuid.UUID
    relation: str
    dst_entity_id: uuid.UUID
    valid_from: datetime | None = None
    evidence_item_id: uuid.UUID | None = None


class UnrelateEntities(_Op):
    type: Literal["unrelate_entities"] = "unrelate_entities"
    relation_id: uuid.UUID


class SetTriggerState(_Op):
    type: Literal["set_trigger_state"] = "set_trigger_state"
    trigger_id: uuid.UUID
    state: TriggerState


class UpdateTrigger(_Op):
    type: Literal["update_trigger"] = "update_trigger"
    trigger_id: uuid.UUID
    changes: dict[str, Any]


Op = Annotated[
    CreateItem
    | UpdateItem
    | SetItemState
    | SupersedeItem
    | CorrectItem
    | FulfilIntention
    | DeleteItem
    | RestoreItem
    | LinkItems
    | UnlinkItems
    | AttachEntity
    | DetachEntity
    | UpsertEntity
    | DeleteEntity
    | RelateEntities
    | UnrelateEntities
    | SetTriggerState
    | UpdateTrigger,
    Field(discriminator="type"),
]

OP_ADAPTER: TypeAdapter[Op] = TypeAdapter(Op)

# The write-log label of each op (the vocabulary of S2.2).
OP_LABELS: dict[str, WriteOp] = {
    "create_item": WriteOp.CREATE,
    "update_item": WriteOp.UPDATE,
    "set_item_state": WriteOp.SET_STATE,
    "supersede_item": WriteOp.SUPERSEDE,
    "correct_item": WriteOp.CORRECT,
    "fulfil_intention": WriteOp.FULFIL,
    "delete_item": WriteOp.DELETE,
    "restore_item": WriteOp.RESTORE,
    "link_items": WriteOp.LINK,
    "unlink_items": WriteOp.UNLINK,
    "attach_entity": WriteOp.LINK,
    "detach_entity": WriteOp.UNLINK,
    "upsert_entity": WriteOp.UPSERT_ENTITY,
    "delete_entity": WriteOp.DELETE,
    "relate_entities": WriteOp.RELATE,
    "unrelate_entities": WriteOp.UNRELATE,
    "set_trigger_state": WriteOp.SET_STATE,
    "update_trigger": WriteOp.UPDATE,
}


def op_label(op: Op) -> WriteOp:
    return OP_LABELS[op.type]


def dump_op(op: Op) -> dict[str, Any]:
    return op.model_dump(mode="json")


def load_op(data: dict[str, Any]) -> Op:
    return OP_ADAPTER.validate_python(data)

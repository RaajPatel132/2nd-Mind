"""Stored shapes of memory rows. Snapshots of these go into the write log and item versions, so
every field is JSON-serialisable and every change can be replayed backwards."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from secondmind.core import (
    EntityKind,
    EntityRole,
    ItemStatus,
    KeyKind,
    Kind,
    Layer,
    LinkType,
    Modality,
    PolicyDecision,
    ResourceFormat,
    Sensitivity,
    Source,
    TargetType,
    TimePrecision,
    TriggerOn,
    TriggerState,
    Trust,
    VocabKind,
    WriteOp,
)


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ItemContent(_Record):
    """Everything about a memory item that the writer may set or change."""

    status: ItemStatus = ItemStatus.ACTIVE
    source: Source = Source.USER_MESSAGE
    trust: Trust = Trust.USER_STATED
    raw_content: str | None = None
    content_ref: str | None = None
    kind: Kind
    subtype: str | None = None
    format: ResourceFormat | None = None
    state: str
    text: str
    title: str
    summary: str | None = None
    category_id: uuid.UUID | None = None
    tags: list[str] = []
    attributes: dict[str, Any] = {}
    enrichment: dict[str, Any] = {}
    rationale: str | None = None
    subject_entity_id: uuid.UUID | None = None
    predicate: str | None = None
    value: dict[str, Any] | None = None
    mentioned_at: datetime
    occurred_start: datetime | None = None
    occurred_end: datetime | None = None
    time_precision: TimePrecision | None = None
    rrule: str | None = None
    due_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    modality: Modality = Modality.ASSERTED
    confidence: float = Field(default=1.0, ge=0, le=1)
    sentiment: int | None = Field(default=None, ge=-2, le=2)
    rating: dict[str, float] | None = None
    sensitivity: Sensitivity = Sensitivity.NORMAL
    importance: int = Field(default=3, ge=1, le=5)
    access_count: int = 0
    last_accessed_at: datetime | None = None
    in_core: bool = False
    core_confirmed_at: datetime | None = None
    in_quick: bool = False
    quick_reason: str | None = None
    quick_until: datetime | None = None


# Fields a later write may change (everything in ItemContent).
ITEM_FIELDS: frozenset[str] = frozenset(ItemContent.model_fields)


class ItemRecord(ItemContent):
    id: uuid.UUID
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    created_by_turn_id: uuid.UUID
    updated_by_turn_id: uuid.UUID

    @property
    def layer(self) -> Layer:
        if self.in_core:
            return Layer.CORE
        return Layer.QUICK if self.in_quick else Layer.ARCHIVE

    @property
    def content(self) -> ItemContent:
        return ItemContent.model_validate(self.model_dump(include=set(ITEM_FIELDS)))


class EntityContent(_Record):
    kind: EntityKind
    name: str
    aliases: list[str] = []
    labels: list[str] = []
    is_key: bool = False
    attributes: dict[str, Any] = {}
    summary: str | None = None
    summary_updated_at: datetime | None = None
    status: Literal["active", "deleted"] = "active"


ENTITY_FIELDS: frozenset[str] = frozenset(EntityContent.model_fields)


class EntityRecord(EntityContent):
    id: uuid.UUID
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    created_by_turn_id: uuid.UUID | None
    updated_by_turn_id: uuid.UUID | None

    @property
    def display(self) -> str:
        """Name with the first relationship label: "Nisha (sister)"."""
        if self.kind is EntityKind.SELF:
            return "me"
        label = next((lb for lb in self.labels if lb.lower() != self.name.lower()), None)
        return f"{self.name} ({label})" if label else self.name


class ItemEntityRecord(_Record):
    id: uuid.UUID
    workspace_id: uuid.UUID
    item_id: uuid.UUID
    entity_id: uuid.UUID
    role: EntityRole
    created_by_turn_id: uuid.UUID


class LinkRecord(_Record):
    id: uuid.UUID
    workspace_id: uuid.UUID
    src_item_id: uuid.UUID
    link_type: LinkType
    dst_item_id: uuid.UUID
    created_by_turn_id: uuid.UUID


class RelationRecord(_Record):
    id: uuid.UUID
    workspace_id: uuid.UUID
    src_entity_id: uuid.UUID
    relation: str
    dst_entity_id: uuid.UUID
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    evidence_item_id: uuid.UUID | None = None
    created_by_turn_id: uuid.UUID


class TriggerContent(_Record):
    on: TriggerOn = TriggerOn.TIME
    spec: dict[str, Any] = {}
    fires_at: datetime | None = None
    state: TriggerState = TriggerState.PENDING
    expires_at: datetime | None = None


class TriggerRecord(TriggerContent):
    id: uuid.UUID
    workspace_id: uuid.UUID
    item_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    created_by_turn_id: uuid.UUID
    updated_by_turn_id: uuid.UUID


class CategoryRecord(_Record):
    id: uuid.UUID
    workspace_id: uuid.UUID
    slug: str
    display_name: str
    aliases: list[str] = []


class VocabRecord(_Record):
    id: uuid.UUID
    workspace_id: uuid.UUID
    vocab: VocabKind
    slug: str
    aliases: list[str] = []


class KeyRecord(_Record):
    id: uuid.UUID
    workspace_id: uuid.UUID
    item_id: uuid.UUID
    key_kind: KeyKind
    text: str
    content_hash: str
    embedding: list[float] | None = None
    embedding_model: str | None = None


class WriteLogRecord(_Record):
    """One primitive change (or a refused one) made by a turn. Replayed backwards by undo."""

    turn_id: uuid.UUID
    seq: int
    op: WriteOp
    target_type: TargetType
    target_id: uuid.UUID
    layer: Layer
    title: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    rationale: str = ""
    trust: Trust = Trust.USER_STATED
    decision: PolicyDecision
    rule_id: str
    reason: str = ""


class HeldWriteRecord(_Record):
    id: uuid.UUID
    workspace_id: uuid.UUID
    turn_id: uuid.UUID
    ops: list[dict[str, Any]]
    rule_id: str
    reason: str
    title: str
    layer: Layer
    status: Literal["pending", "confirmed", "rejected"] = "pending"
    resolved_turn_id: uuid.UUID | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class VersionRecord(_Record):
    item_id: uuid.UUID
    turn_id: uuid.UUID
    version: int
    snapshot: dict[str, Any]

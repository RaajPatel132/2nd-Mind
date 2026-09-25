"""Memory tables (S2.1, ADR-0018). Every one is workspace-owned and protected by RLS (see the 0002
migration). Items have no search columns: all search goes through ``memory_keys``."""

import os
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    cast,
    func,
    type_coerce,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.types import UserDefinedType

from secondmind.memory.adapters.db import Base

# The embedding size the column was migrated with. Start-up checks it against EMBED_DIMENSIONS.
EMBED_DIMENSIONS = int(os.environ.get("EMBED_DIMENSIONS", "1536") or "1536")


class Vector(UserDefinedType[list[float]]):
    """pgvector ``vector(n)``, sent and read as text so no driver codec is needed."""

    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **_: Any) -> str:
        return f"vector({self.dim})"

    def bind_expression(self, bindvalue: Any) -> ColumnElement[Any]:
        return func.cast(bindvalue, self)

    def column_expression(self, colexpr: Any) -> ColumnElement[Any]:
        # Keep this type on the text cast, so result_processor parses the value.
        return type_coerce(cast(colexpr, Text), self)

    def bind_processor(self, dialect: Any) -> Any:
        def process(value: list[float] | None) -> str | None:
            return None if value is None else "[" + ",".join(f"{float(v):.7g}" for v in value) + "]"

        return process

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        def process(value: str | None) -> list[float] | None:
            if value is None:
                return None
            return [float(v) for v in value.strip("[]").split(",") if v]

        return process


def _ts(*, nullable: bool = False, default: bool = False) -> Mapped[Any]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=nullable,
        server_default=func.now() if default else None,
    )


def _turn_fk(column: str, table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        [column, "workspace_id"], ["turns.id", "turns.workspace_id"], ondelete="CASCADE"
    )


KIND_STATE_CHECK = (
    "(kind IN ('fact', 'preference') AND state IN ('current', 'superseded')) OR "
    "(kind = 'episode' AND state = 'happened') OR "
    "(kind = 'plan' AND state IN ('scheduled', 'happened', 'moved', 'cancelled')) OR "
    "(kind = 'task' AND state IN ('open', 'done', 'dropped')) OR "
    "(kind = 'intention' AND state IN ('wanted', 'active', 'fulfilled', 'dropped')) OR "
    "(kind = 'resource' AND state IN ('saved', 'consumed')) OR "
    "(kind = 'note' AND state = 'current') OR "
    "(kind = 'rule' AND state IN ('proposed', 'active', 'retired')) OR "
    "(kind = 'pattern' AND state IN ('proposed', 'confirmed', 'retired'))"
)


class EntityRow(Base):
    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    labels: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    is_key: Mapped[bool] = mapped_column(Boolean, server_default="false")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    summary: Mapped[str | None] = mapped_column(Text)
    summary_updated_at: Mapped[datetime | None] = _ts(nullable=True)
    status: Mapped[str] = mapped_column(String(16), server_default="active")
    created_at: Mapped[datetime] = _ts(default=True)
    updated_at: Mapped[datetime] = _ts(default=True)
    created_by_turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_by_turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        CheckConstraint(
            "kind IN ('self', 'person', 'place', 'org', 'thing', 'work', 'topic', 'project', "
            "'list')",
            name="kind",
        ),
        CheckConstraint("status IN ('active', 'deleted')", name="status"),
        Index("ix_entities_workspace_id_kind", "workspace_id", "kind"),
        # Exactly one ``self`` per workspace.
        Index(
            "uq_entities_workspace_id_self",
            "workspace_id",
            unique=True,
            postgresql_where=sql_text("kind = 'self'"),
        ),
    )


class CategoryRow(Base):
    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    slug: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    created_at: Mapped[datetime] = _ts(default=True)

    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("workspace_id", "slug"),
    )


class VocabTermRow(Base):
    __tablename__ = "vocab_terms"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    vocab: Mapped[str] = mapped_column(String(16))
    slug: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    created_at: Mapped[datetime] = _ts(default=True)

    __table_args__ = (
        UniqueConstraint("workspace_id", "vocab", "slug"),
        CheckConstraint("vocab IN ('subtype', 'predicate', 'relation')", name="vocab"),
    )


class MemoryItemRow(Base):
    __tablename__ = "memory_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = _ts(default=True)
    updated_at: Mapped[datetime] = _ts(default=True)
    created_by_turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    updated_by_turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source: Mapped[str] = mapped_column(String(16))
    trust: Mapped[str] = mapped_column(String(16))
    raw_content: Mapped[str | None] = mapped_column(Text)
    content_ref: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="active")
    # What it is
    kind: Mapped[str] = mapped_column(String(16))
    subtype: Mapped[str | None] = mapped_column(Text)
    format: Mapped[str | None] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16))
    # Content
    text: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    tags: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    enrichment: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    rationale: Mapped[str | None] = mapped_column(Text)
    # Structured statement
    subject_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    predicate: Mapped[str | None] = mapped_column(Text)
    value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Three clocks
    mentioned_at: Mapped[datetime] = _ts()
    occurred_start: Mapped[datetime | None] = _ts(nullable=True)
    occurred_end: Mapped[datetime | None] = _ts(nullable=True)
    time_precision: Mapped[str | None] = mapped_column(String(16))
    rrule: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime | None] = _ts(nullable=True)
    valid_from: Mapped[datetime | None] = _ts(nullable=True)
    valid_to: Mapped[datetime | None] = _ts(nullable=True)
    # Epistemics
    modality: Mapped[str] = mapped_column(String(16), server_default="asserted")
    confidence: Mapped[float] = mapped_column(Float, server_default="1")
    sentiment: Mapped[int | None] = mapped_column(SmallInteger)
    rating: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    sensitivity: Mapped[str] = mapped_column(String(16), server_default="normal")
    importance: Mapped[int] = mapped_column(SmallInteger, server_default="3")
    access_count: Mapped[int] = mapped_column(Integer, server_default="0")
    last_accessed_at: Mapped[datetime | None] = _ts(nullable=True)
    # Layers (archive is implicit: every active item is in it)
    in_core: Mapped[bool] = mapped_column(Boolean, server_default="false")
    core_confirmed_at: Mapped[datetime | None] = _ts(nullable=True)
    in_quick: Mapped[bool] = mapped_column(Boolean, server_default="false")
    quick_reason: Mapped[str | None] = mapped_column(Text)
    quick_until: Mapped[datetime | None] = _ts(nullable=True)

    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        _turn_fk("created_by_turn_id", "memory_items"),
        _turn_fk("updated_by_turn_id", "memory_items"),
        ForeignKeyConstraint(
            ["subject_entity_id", "workspace_id"], ["entities.id", "entities.workspace_id"]
        ),
        ForeignKeyConstraint(
            ["category_id", "workspace_id"], ["categories.id", "categories.workspace_id"]
        ),
        CheckConstraint(
            "kind IN ('fact', 'preference', 'episode', 'plan', 'task', 'intention', "
            "'resource', 'note', 'rule', 'pattern')",
            name="kind",
        ),
        CheckConstraint(KIND_STATE_CHECK, name="state_for_kind"),
        CheckConstraint(
            "format IS NULL OR (kind = 'resource' AND format IN ('article', 'video', 'pdf', "
            "'image', 'link', 'other'))",
            name="format",
        ),
        CheckConstraint("status IN ('active', 'archived', 'deleted')", name="status"),
        CheckConstraint("source IN ('user_message', 'link', 'file', 'derived')", name="source"),
        CheckConstraint("trust IN ('user_stated', 'content_derived')", name="trust"),
        CheckConstraint(
            "modality IN ('asserted', 'planned', 'hypothetical', 'reported')", name="modality"
        ),
        CheckConstraint(
            "sensitivity IN ('normal', 'personal', 'sensitive', 'secret')", name="sensitivity"
        ),
        CheckConstraint(
            "time_precision IS NULL OR time_precision IN ('datetime', 'day', 'month', 'year')",
            name="time_precision",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence"),
        CheckConstraint("sentiment IS NULL OR sentiment BETWEEN -2 AND 2", name="sentiment"),
        CheckConstraint("importance BETWEEN 1 AND 5", name="importance"),
        Index("ix_memory_items_workspace_id_kind_state", "workspace_id", "kind", "state"),
        # "Latest value" is one indexed lookup.
        Index(
            "ix_memory_items_subject_predicate_current",
            "subject_entity_id",
            "predicate",
            postgresql_where=sql_text("valid_to IS NULL"),
        ),
        Index(
            "ix_memory_items_workspace_id_in_core",
            "workspace_id",
            postgresql_where=sql_text("in_core"),
        ),
        Index(
            "ix_memory_items_workspace_id_quick_until",
            "workspace_id",
            "quick_until",
            postgresql_where=sql_text("in_quick"),
        ),
    )


class MemoryEntityRow(Base):
    __tablename__ = "memory_entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    role: Mapped[str] = mapped_column(String(16))
    created_by_turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _ts(default=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["item_id", "workspace_id"],
            ["memory_items.id", "memory_items.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["entity_id", "workspace_id"],
            ["entities.id", "entities.workspace_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("item_id", "entity_id", "role"),
        CheckConstraint(
            "role IN ('about', 'with', 'for', 'by', 'at', 'owner', 'part_of')", name="role"
        ),
        Index("ix_memory_entities_entity_id", "entity_id"),
        Index("ix_memory_entities_workspace_id", "workspace_id"),
    )


class MemoryLinkRow(Base):
    __tablename__ = "memory_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    src_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    link_type: Mapped[str] = mapped_column(String(16))
    dst_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_by_turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _ts(default=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["src_item_id", "workspace_id"],
            ["memory_items.id", "memory_items.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["dst_item_id", "workspace_id"],
            ["memory_items.id", "memory_items.workspace_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("src_item_id", "link_type", "dst_item_id"),
        CheckConstraint(
            "link_type IN ('supersedes', 'fulfils', 'part_of', 'follows', 'because', "
            "'evidence_for', 'duplicate_of', 'derived_from', 'gift_for_event')",
            name="link_type",
        ),
        Index("ix_memory_links_dst_item_id", "dst_item_id"),
        Index("ix_memory_links_workspace_id", "workspace_id"),
    )


class EntityRelationRow(Base):
    __tablename__ = "entity_relations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    src_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    relation: Mapped[str] = mapped_column(Text)
    dst_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    valid_from: Mapped[datetime | None] = _ts(nullable=True)
    valid_to: Mapped[datetime | None] = _ts(nullable=True)
    evidence_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by_turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _ts(default=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["src_entity_id", "workspace_id"],
            ["entities.id", "entities.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["dst_entity_id", "workspace_id"],
            ["entities.id", "entities.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["evidence_item_id", "workspace_id"],
            ["memory_items.id", "memory_items.workspace_id"],
        ),
        Index("ix_entity_relations_src_entity_id", "src_entity_id"),
        Index("ix_entity_relations_dst_entity_id", "dst_entity_id"),
        Index("ix_entity_relations_workspace_id", "workspace_id"),
    )


class MemoryKeyRow(Base):
    __tablename__ = "memory_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    key_kind: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIMENSIONS))
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )
    created_at: Mapped[datetime] = _ts(default=True)
    updated_at: Mapped[datetime] = _ts(default=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["item_id", "workspace_id"],
            ["memory_items.id", "memory_items.workspace_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "key_kind IN ('text', 'verbal', 'alt', 'cue', 'question', 'change')", name="key_kind"
        ),
        Index("ix_memory_keys_tsv", "tsv", postgresql_using="gin"),
        Index("ix_memory_keys_item_id", "item_id"),
        Index("ix_memory_keys_workspace_id_content_hash", "workspace_id", "content_hash"),
    )


class TriggerRow(Base):
    __tablename__ = "triggers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    on: Mapped[str] = mapped_column(String(16))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    fires_at: Mapped[datetime | None] = _ts(nullable=True)
    state: Mapped[str] = mapped_column(String(16), server_default="pending")
    expires_at: Mapped[datetime | None] = _ts(nullable=True)
    created_at: Mapped[datetime] = _ts(default=True)
    updated_at: Mapped[datetime] = _ts(default=True)
    created_by_turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    updated_by_turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["item_id", "workspace_id"],
            ["memory_items.id", "memory_items.workspace_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("\"on\" IN ('time', 'person', 'place', 'topic', 'situation')", name="on"),
        CheckConstraint(
            "state IN ('pending', 'fired', 'done', 'cancelled', 'expired')", name="state"
        ),
        Index("ix_triggers_item_id", "item_id"),
        Index(
            "ix_triggers_workspace_id_fires_at",
            "workspace_id",
            "fires_at",
            postgresql_where=sql_text("state = 'pending'"),
        ),
    )


class ItemVersionRow(Base):
    __tablename__ = "item_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _ts(default=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["item_id", "workspace_id"],
            ["memory_items.id", "memory_items.workspace_id"],
            ondelete="CASCADE",
        ),
        _turn_fk("turn_id", "item_versions"),
        UniqueConstraint("item_id", "version"),
        Index("ix_item_versions_workspace_id", "workspace_id"),
    )


class WriteLogRow(Base):
    __tablename__ = "write_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    seq: Mapped[int] = mapped_column(Integer)
    op: Mapped[str] = mapped_column(String(16))
    target_type: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    layer: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text, server_default="")
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    rationale: Mapped[str] = mapped_column(Text, server_default="")
    trust: Mapped[str] = mapped_column(String(16))
    decision: Mapped[str] = mapped_column(String(16))
    rule_id: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text, server_default="")
    created_at: Mapped[datetime] = _ts(default=True)

    __table_args__ = (
        _turn_fk("turn_id", "write_log"),
        UniqueConstraint("turn_id", "seq"),
        CheckConstraint("decision IN ('allowed', 'held', 'blocked')", name="decision"),
        Index("ix_write_log_target_id", "target_id"),
        Index("ix_write_log_workspace_id", "workspace_id"),
    )


class HeldWriteRow(Base):
    __tablename__ = "held_writes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    ops: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    rule_id: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    layer: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    resolved_turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _ts(default=True)
    resolved_at: Mapped[datetime | None] = _ts(nullable=True)

    __table_args__ = (
        _turn_fk("turn_id", "held_writes"),
        CheckConstraint("status IN ('pending', 'confirmed', 'rejected')", name="status"),
        Index("ix_held_writes_workspace_id_status", "workspace_id", "status"),
    )


WORKSPACE_OWNED_MEMORY_TABLES = (
    "entities",
    "categories",
    "vocab_terms",
    "memory_items",
    "memory_entities",
    "memory_links",
    "entity_relations",
    "memory_keys",
    "triggers",
    "item_versions",
    "write_log",
    "held_writes",
)

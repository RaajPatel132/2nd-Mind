"""Memory: items, entities, relations, links, keys, triggers, vocab, versions, the write log and
held writes (S2.1, ADR-0018), each workspace-owned under RLS. Every workspace gets a ``self``
entity from a trigger, and existing workspaces are backfilled.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-24
"""

import os
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.types import UserDefinedType

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREDICATE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
# Start-up refuses to run if EMBED_DIMENSIONS no longer matches the migrated column.
EMBED_DIMENSIONS = int(os.environ.get("EMBED_DIMENSIONS", "1536") or "1536")
WORKSPACE_OWNED = (
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


class Vector(UserDefinedType[Any]):
    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **_: Any) -> str:
        return f"vector({self.dim})"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "categories",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("aliases", pg.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_categories_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_categories_id_workspace_id")),
        sa.UniqueConstraint("workspace_id", "slug", name=op.f("uq_categories_workspace_id_slug")),
    )
    op.create_table(
        "entities",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("aliases", pg.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("labels", pg.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("is_key", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "attributes", pg.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summary_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_turn_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_turn_id", pg.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('self', 'person', 'place', 'org', 'thing', 'work', 'topic', 'project', "
            "'list')",
            name=op.f("ck_entities_kind"),
        ),
        sa.CheckConstraint("status IN ('active', 'deleted')", name=op.f("ck_entities_status")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_entities_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entities")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_entities_id_workspace_id")),
    )
    op.create_index(
        "ix_entities_workspace_id_kind", "entities", ["workspace_id", "kind"], unique=False
    )
    op.create_index(
        "uq_entities_workspace_id_self",
        "entities",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'self'"),
    )
    op.create_table(
        "vocab_terms",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("vocab", sa.String(length=16), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("aliases", pg.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "vocab IN ('subtype', 'predicate', 'relation')", name=op.f("ck_vocab_terms_vocab")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_vocab_terms_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vocab_terms")),
        sa.UniqueConstraint(
            "workspace_id", "vocab", "slug", name=op.f("uq_vocab_terms_workspace_id_vocab_slug")
        ),
    )
    op.create_table(
        "held_writes",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("ops", pg.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rule_id", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("layer", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("resolved_turn_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'rejected')", name=op.f("ck_held_writes_status")
        ),
        sa.ForeignKeyConstraint(
            ["turn_id", "workspace_id"],
            ["turns.id", "turns.workspace_id"],
            name=op.f("fk_held_writes_turn_id_workspace_id_turns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_held_writes")),
    )
    op.create_index(
        "ix_held_writes_workspace_id_status",
        "held_writes",
        ["workspace_id", "status"],
        unique=False,
    )
    op.create_table(
        "memory_items",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_turn_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by_turn_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("trust", sa.String(length=16), nullable=False),
        sa.Column("raw_content", sa.Text(), nullable=True),
        sa.Column("content_ref", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("subtype", sa.Text(), nullable=True),
        sa.Column("format", sa.String(length=16), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("category_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("tags", pg.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column(
            "attributes", pg.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "enrichment", pg.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("subject_entity_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("predicate", sa.Text(), nullable=True),
        sa.Column("value", pg.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("mentioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurred_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_precision", sa.String(length=16), nullable=True),
        sa.Column("rrule", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("modality", sa.String(length=16), server_default="asserted", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="1", nullable=False),
        sa.Column("sentiment", sa.SmallInteger(), nullable=True),
        sa.Column("rating", pg.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sensitivity", sa.String(length=16), server_default="normal", nullable=False),
        sa.Column("importance", sa.SmallInteger(), server_default="3", nullable=False),
        sa.Column("access_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("in_core", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("core_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("in_quick", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("quick_reason", sa.Text(), nullable=True),
        sa.Column("quick_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(kind IN ('fact', 'preference') AND state IN ('current', 'superseded')) OR (kind = "
            "'episode' AND state = 'happened') OR (kind = 'plan' AND state IN ('scheduled', "
            "'happened', 'moved', 'cancelled')) OR (kind = 'task' AND state IN ('open', 'done', "
            "'dropped')) OR (kind = 'intention' AND state IN ('wanted', 'active', 'fulfilled', "
            "'dropped')) OR (kind = 'resource' AND state IN ('saved', 'consumed')) OR (kind = "
            "'note' AND state = 'current') OR (kind = 'rule' AND state IN ('proposed', 'active', "
            "'retired')) OR (kind = 'pattern' AND state IN ('proposed', 'confirmed', 'retired'))",
            name=op.f("ck_memory_items_state_for_kind"),
        ),
        sa.CheckConstraint(
            "format IS NULL OR (kind = 'resource' AND format IN ('article', 'video', 'pdf', "
            "'image', 'link', 'other'))",
            name=op.f("ck_memory_items_format"),
        ),
        sa.CheckConstraint(
            "kind IN ('fact', 'preference', 'episode', 'plan', 'task', 'intention', 'resource', "
            "'note', 'rule', 'pattern')",
            name=op.f("ck_memory_items_kind"),
        ),
        sa.CheckConstraint(
            "modality IN ('asserted', 'planned', 'hypothetical', 'reported')",
            name=op.f("ck_memory_items_modality"),
        ),
        sa.CheckConstraint(
            "sensitivity IN ('normal', 'personal', 'sensitive', 'secret')",
            name=op.f("ck_memory_items_sensitivity"),
        ),
        sa.CheckConstraint(
            "source IN ('user_message', 'link', 'file', 'derived')",
            name=op.f("ck_memory_items_source"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived', 'deleted')", name=op.f("ck_memory_items_status")
        ),
        sa.CheckConstraint(
            "time_precision IS NULL OR time_precision IN ('datetime', 'day', 'month', 'year')",
            name=op.f("ck_memory_items_time_precision"),
        ),
        sa.CheckConstraint(
            "trust IN ('user_stated', 'content_derived')", name=op.f("ck_memory_items_trust")
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name=op.f("ck_memory_items_confidence")
        ),
        sa.CheckConstraint("importance BETWEEN 1 AND 5", name=op.f("ck_memory_items_importance")),
        sa.CheckConstraint(
            "sentiment IS NULL OR sentiment BETWEEN -2 AND 2",
            name=op.f("ck_memory_items_sentiment"),
        ),
        sa.ForeignKeyConstraint(
            ["category_id", "workspace_id"],
            ["categories.id", "categories.workspace_id"],
            name=op.f("fk_memory_items_category_id_workspace_id_categories"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_turn_id", "workspace_id"],
            ["turns.id", "turns.workspace_id"],
            name=op.f("fk_memory_items_created_by_turn_id_workspace_id_turns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subject_entity_id", "workspace_id"],
            ["entities.id", "entities.workspace_id"],
            name=op.f("fk_memory_items_subject_entity_id_workspace_id_entities"),
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_turn_id", "workspace_id"],
            ["turns.id", "turns.workspace_id"],
            name=op.f("fk_memory_items_updated_by_turn_id_workspace_id_turns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_memory_items_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_items")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_memory_items_id_workspace_id")),
    )
    op.create_index(
        "ix_memory_items_subject_predicate_current",
        "memory_items",
        ["subject_entity_id", "predicate"],
        unique=False,
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.create_index(
        "ix_memory_items_workspace_id_in_core",
        "memory_items",
        ["workspace_id"],
        unique=False,
        postgresql_where=sa.text("in_core"),
    )
    op.create_index(
        "ix_memory_items_workspace_id_kind_state",
        "memory_items",
        ["workspace_id", "kind", "state"],
        unique=False,
    )
    op.create_index(
        "ix_memory_items_workspace_id_quick_until",
        "memory_items",
        ["workspace_id", "quick_until"],
        unique=False,
        postgresql_where=sa.text("in_quick"),
    )
    op.create_table(
        "write_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("op", sa.String(length=16), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("layer", sa.String(length=16), nullable=False),
        sa.Column("title", sa.Text(), server_default="", nullable=False),
        sa.Column("before", pg.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", pg.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rationale", sa.Text(), server_default="", nullable=False),
        sa.Column("trust", sa.String(length=16), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("rule_id", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('allowed', 'held', 'blocked')", name=op.f("ck_write_log_decision")
        ),
        sa.ForeignKeyConstraint(
            ["turn_id", "workspace_id"],
            ["turns.id", "turns.workspace_id"],
            name=op.f("fk_write_log_turn_id_workspace_id_turns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_write_log")),
        sa.UniqueConstraint("turn_id", "seq", name=op.f("uq_write_log_turn_id_seq")),
    )
    op.create_index("ix_write_log_target_id", "write_log", ["target_id"], unique=False)
    op.create_index("ix_write_log_workspace_id", "write_log", ["workspace_id"], unique=False)
    op.create_table(
        "entity_relations",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("src_entity_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.Column("dst_entity_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_item_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_turn_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dst_entity_id", "workspace_id"],
            ["entities.id", "entities.workspace_id"],
            name=op.f("fk_entity_relations_dst_entity_id_workspace_id_entities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_item_id", "workspace_id"],
            ["memory_items.id", "memory_items.workspace_id"],
            name=op.f("fk_entity_relations_evidence_item_id_workspace_id_memory_items"),
        ),
        sa.ForeignKeyConstraint(
            ["src_entity_id", "workspace_id"],
            ["entities.id", "entities.workspace_id"],
            name=op.f("fk_entity_relations_src_entity_id_workspace_id_entities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entity_relations")),
    )
    op.create_index(
        "ix_entity_relations_dst_entity_id", "entity_relations", ["dst_entity_id"], unique=False
    )
    op.create_index(
        "ix_entity_relations_src_entity_id", "entity_relations", ["src_entity_id"], unique=False
    )
    op.create_index(
        "ix_entity_relations_workspace_id", "entity_relations", ["workspace_id"], unique=False
    )
    op.create_table(
        "item_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", pg.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["item_id", "workspace_id"],
            ["memory_items.id", "memory_items.workspace_id"],
            name=op.f("fk_item_versions_item_id_workspace_id_memory_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id", "workspace_id"],
            ["turns.id", "turns.workspace_id"],
            name=op.f("fk_item_versions_turn_id_workspace_id_turns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item_versions")),
        sa.UniqueConstraint("item_id", "version", name=op.f("uq_item_versions_item_id_version")),
    )
    op.create_index(
        "ix_item_versions_workspace_id", "item_versions", ["workspace_id"], unique=False
    )
    op.create_table(
        "memory_entities",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_by_turn_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('about', 'with', 'for', 'by', 'at', 'owner', 'part_of')",
            name=op.f("ck_memory_entities_role"),
        ),
        sa.ForeignKeyConstraint(
            ["entity_id", "workspace_id"],
            ["entities.id", "entities.workspace_id"],
            name=op.f("fk_memory_entities_entity_id_workspace_id_entities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id", "workspace_id"],
            ["memory_items.id", "memory_items.workspace_id"],
            name=op.f("fk_memory_entities_item_id_workspace_id_memory_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_entities")),
        sa.UniqueConstraint(
            "item_id", "entity_id", "role", name=op.f("uq_memory_entities_item_id_entity_id_role")
        ),
    )
    op.create_index("ix_memory_entities_entity_id", "memory_entities", ["entity_id"], unique=False)
    op.create_index(
        "ix_memory_entities_workspace_id", "memory_entities", ["workspace_id"], unique=False
    )
    op.create_table(
        "memory_keys",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("key_kind", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(EMBED_DIMENSIONS), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column(
            "tsv",
            pg.TSVECTOR(),
            sa.Computed("to_tsvector('english', text)", persisted=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "key_kind IN ('text', 'verbal', 'alt', 'cue', 'question', 'change')",
            name=op.f("ck_memory_keys_key_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id", "workspace_id"],
            ["memory_items.id", "memory_items.workspace_id"],
            name=op.f("fk_memory_keys_item_id_workspace_id_memory_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_keys")),
    )
    op.create_index("ix_memory_keys_item_id", "memory_keys", ["item_id"], unique=False)
    op.create_index(
        "ix_memory_keys_tsv", "memory_keys", ["tsv"], unique=False, postgresql_using="gin"
    )
    op.create_index(
        "ix_memory_keys_workspace_id_content_hash",
        "memory_keys",
        ["workspace_id", "content_hash"],
        unique=False,
    )
    op.create_table(
        "memory_links",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("src_item_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("link_type", sa.String(length=16), nullable=False),
        sa.Column("dst_item_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_turn_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "link_type IN ('supersedes', 'fulfils', 'part_of', 'follows', 'because', "
            "'evidence_for', 'duplicate_of', 'derived_from', 'gift_for_event')",
            name=op.f("ck_memory_links_link_type"),
        ),
        sa.ForeignKeyConstraint(
            ["dst_item_id", "workspace_id"],
            ["memory_items.id", "memory_items.workspace_id"],
            name=op.f("fk_memory_links_dst_item_id_workspace_id_memory_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["src_item_id", "workspace_id"],
            ["memory_items.id", "memory_items.workspace_id"],
            name=op.f("fk_memory_links_src_item_id_workspace_id_memory_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_links")),
        sa.UniqueConstraint(
            "src_item_id",
            "link_type",
            "dst_item_id",
            name=op.f("uq_memory_links_src_item_id_link_type_dst_item_id"),
        ),
    )
    op.create_index("ix_memory_links_dst_item_id", "memory_links", ["dst_item_id"], unique=False)
    op.create_index("ix_memory_links_workspace_id", "memory_links", ["workspace_id"], unique=False)
    op.create_table(
        "triggers",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("on", sa.String(length=16), nullable=False),
        sa.Column("spec", pg.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("fires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_turn_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by_turn_id", pg.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('pending', 'fired', 'done', 'cancelled', 'expired')",
            name=op.f("ck_triggers_state"),
        ),
        sa.CheckConstraint(
            "\"on\" IN ('time', 'person', 'place', 'topic', 'situation')",
            name=op.f("ck_triggers_on"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id", "workspace_id"],
            ["memory_items.id", "memory_items.workspace_id"],
            name=op.f("fk_triggers_item_id_workspace_id_memory_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_triggers")),
    )
    op.create_index("ix_triggers_item_id", "triggers", ["item_id"], unique=False)
    op.create_index(
        "ix_triggers_workspace_id_fires_at",
        "triggers",
        ["workspace_id", "fires_at"],
        unique=False,
        postgresql_where=sa.text("state = 'pending'"),
    )
    op.add_column(
        "turns", sa.Column("kind", sa.String(length=16), server_default="user", nullable=False)
    )
    op.create_check_constraint(
        "ck_turns_kind", "turns", "kind IN ('user', 'undo', 'confirm', 'system')"
    )
    op.add_column("turns", sa.Column("parent_turn_id", pg.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "workspaces",
        sa.Column("default_lead_minutes", sa.Integer(), server_default="1440", nullable=False),
    )

    for table in WORKSPACE_OWNED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY workspace_isolation ON {table} "
            f"USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
        )

    # Exactly one ``self`` entity per workspace, created with the workspace. The function runs
    # as the schema owner, so it can insert the row before any workspace scope is set.
    op.execute(
        """
        CREATE FUNCTION create_self_entity() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        BEGIN
            INSERT INTO entities (id, workspace_id, kind, name)
            VALUES (gen_random_uuid(), NEW.id, 'self', 'me');
            RETURN NEW;
        END $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION create_self_entity() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER workspaces_self_entity AFTER INSERT ON workspaces "
        "FOR EACH ROW EXECUTE FUNCTION create_self_entity()"
    )
    op.execute(
        "INSERT INTO entities (id, workspace_id, kind, name) "
        "SELECT gen_random_uuid(), w.id, 'self', 'me' FROM workspaces w "
        "WHERE NOT EXISTS (SELECT 1 FROM entities e WHERE e.workspace_id = w.id "
        "AND e.kind = 'self')"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS workspaces_self_entity ON workspaces")
    op.execute("DROP FUNCTION IF EXISTS create_self_entity()")
    op.drop_column("workspaces", "default_lead_minutes")
    op.drop_column("turns", "parent_turn_id")
    op.drop_constraint("ck_turns_kind", "turns", type_="check")
    op.drop_column("turns", "kind")
    op.drop_index(
        "ix_triggers_workspace_id_fires_at",
        table_name="triggers",
        postgresql_where=sa.text("state = 'pending'"),
    )
    op.drop_index("ix_triggers_item_id", table_name="triggers")
    op.drop_table("triggers")
    op.drop_index("ix_memory_links_workspace_id", table_name="memory_links")
    op.drop_index("ix_memory_links_dst_item_id", table_name="memory_links")
    op.drop_table("memory_links")
    op.drop_index("ix_memory_keys_workspace_id_content_hash", table_name="memory_keys")
    op.drop_index("ix_memory_keys_tsv", table_name="memory_keys", postgresql_using="gin")
    op.drop_index("ix_memory_keys_item_id", table_name="memory_keys")
    op.drop_table("memory_keys")
    op.drop_index("ix_memory_entities_workspace_id", table_name="memory_entities")
    op.drop_index("ix_memory_entities_entity_id", table_name="memory_entities")
    op.drop_table("memory_entities")
    op.drop_index("ix_item_versions_workspace_id", table_name="item_versions")
    op.drop_table("item_versions")
    op.drop_index("ix_entity_relations_workspace_id", table_name="entity_relations")
    op.drop_index("ix_entity_relations_src_entity_id", table_name="entity_relations")
    op.drop_index("ix_entity_relations_dst_entity_id", table_name="entity_relations")
    op.drop_table("entity_relations")
    op.drop_index("ix_write_log_workspace_id", table_name="write_log")
    op.drop_index("ix_write_log_target_id", table_name="write_log")
    op.drop_table("write_log")
    op.drop_index(
        "ix_memory_items_workspace_id_quick_until",
        table_name="memory_items",
        postgresql_where=sa.text("in_quick"),
    )
    op.drop_index("ix_memory_items_workspace_id_kind_state", table_name="memory_items")
    op.drop_index(
        "ix_memory_items_workspace_id_in_core",
        table_name="memory_items",
        postgresql_where=sa.text("in_core"),
    )
    op.drop_index(
        "ix_memory_items_subject_predicate_current",
        table_name="memory_items",
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.drop_table("memory_items")
    op.drop_index("ix_held_writes_workspace_id_status", table_name="held_writes")
    op.drop_table("held_writes")
    op.drop_table("vocab_terms")
    op.drop_index(
        "uq_entities_workspace_id_self",
        table_name="entities",
        postgresql_where=sa.text("kind = 'self'"),
    )
    op.drop_index("ix_entities_workspace_id_kind", table_name="entities")
    op.drop_table("entities")
    op.drop_table("categories")

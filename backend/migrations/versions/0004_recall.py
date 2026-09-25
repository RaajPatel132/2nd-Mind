"""Recall (S3.2): ``conversation_keys`` (what was said in past turns, searchable) and
``item_access`` (append-only retrieval bookkeeping for "frequently retrieved"), both
workspace-owned under RLS. ``memory_links`` gains ``corrects`` (a row recorded by mistake,
S3.12) and ``turns`` gains the ``edit`` kind (an edit from the glass box or Upcoming).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-26
"""

import os
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.types import UserDefinedType

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREDICATE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
APP_GROUP_ROLE = "secondmind_rw"
EMBED_DIMENSIONS = int(os.environ.get("EMBED_DIMENSIONS", "1536") or "1536")
NEW_TABLES = ("conversation_keys", "item_access")

LINKS_BEFORE = (
    "link_type IN ('supersedes', 'fulfils', 'part_of', 'follows', 'because', "
    "'evidence_for', 'duplicate_of', 'derived_from', 'gift_for_event')"
)
LINKS_AFTER = (
    "link_type IN ('supersedes', 'fulfils', 'part_of', 'follows', 'because', "
    "'evidence_for', 'duplicate_of', 'derived_from', 'gift_for_event', 'corrects')"
)
KINDS_BEFORE = "kind IN ('user', 'undo', 'confirm', 'system')"
KINDS_AFTER = "kind IN ('user', 'undo', 'confirm', 'edit', 'system')"


class Vector(UserDefinedType[Any]):
    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **_: Any) -> str:
        return f"vector({self.dim})"


def _replace_check(table: str, name: str, condition: str, *, as_0002: bool = False) -> None:
    """Swap a CHECK constraint. 0002 named ``turns``' one through the naming convention twice
    (``ck_turns_ck_turns_kind``), so either spelling is dropped. Going up, the new one gets the
    name the models use; going down, the name 0002's own downgrade expects."""
    for old in (f"ck_{table}_{name}", f"ck_{table}_ck_{table}_{name}"):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {old}")
    final = f"ck_{table}_ck_{table}_{name}" if as_0002 else f"ck_{table}_{name}"
    op.create_check_constraint(op.f(final), table, condition)


def upgrade() -> None:
    op.create_table(
        "conversation_keys",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("said_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.CheckConstraint("role IN ('user', 'assistant')", name=op.f("ck_conversation_keys_role")),
        sa.ForeignKeyConstraint(
            ["turn_id", "workspace_id"],
            ["turns.id", "turns.workspace_id"],
            name=op.f("fk_conversation_keys_turn_id_workspace_id_turns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_keys")),
        sa.UniqueConstraint(
            "turn_id", "role", "seq", name=op.f("uq_conversation_keys_turn_id_role_seq")
        ),
    )
    op.create_index(
        "ix_conversation_keys_tsv",
        "conversation_keys",
        ["tsv"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_conversation_keys_workspace_id_said_at",
        "conversation_keys",
        ["workspace_id", "said_at"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_keys_workspace_id_content_hash",
        "conversation_keys",
        ["workspace_id", "content_hash"],
        unique=False,
    )
    op.create_table(
        "item_access",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cited", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id", "workspace_id"],
            ["memory_items.id", "memory_items.workspace_id"],
            name=op.f("fk_item_access_item_id_workspace_id_memory_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id", "workspace_id"],
            ["turns.id", "turns.workspace_id"],
            name=op.f("fk_item_access_turn_id_workspace_id_turns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item_access")),
    )
    op.create_index(
        "ix_item_access_workspace_id_item_id_at",
        "item_access",
        ["workspace_id", "item_id", "at"],
        unique=False,
    )
    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY workspace_isolation ON {table} "
            f"USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
        )
    # Append-only for the app: bookkeeping is never rewritten.
    op.execute(f"REVOKE UPDATE, DELETE ON item_access FROM {APP_GROUP_ROLE}")

    _replace_check("memory_links", "link_type", LINKS_AFTER)
    _replace_check("turns", "kind", KINDS_AFTER)


def downgrade() -> None:
    op.execute("UPDATE turns SET kind = 'user' WHERE kind = 'edit'")
    _replace_check("turns", "kind", KINDS_BEFORE, as_0002=True)
    op.execute("DELETE FROM memory_links WHERE link_type = 'corrects'")
    _replace_check("memory_links", "link_type", LINKS_BEFORE)
    op.drop_index("ix_item_access_workspace_id_item_id_at", table_name="item_access")
    op.drop_table("item_access")
    op.drop_index("ix_conversation_keys_workspace_id_content_hash", table_name="conversation_keys")
    op.drop_index("ix_conversation_keys_workspace_id_said_at", table_name="conversation_keys")
    op.drop_index(
        "ix_conversation_keys_tsv", table_name="conversation_keys", postgresql_using="gin"
    )
    op.drop_table("conversation_keys")

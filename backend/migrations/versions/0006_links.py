"""Links (S4.7): ``link_sources`` (where a saved link came from and how reading it went), workspace-
owned under RLS; ``memory_keys`` gains the ``chunk`` kind and a ``position`` (a passage of a saved
page, found by what it says and cited as the page it came from).

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREDICATE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
KEYS_BEFORE = "key_kind IN ('text', 'verbal', 'alt', 'cue', 'question', 'change')"
KEYS_AFTER = "key_kind IN ('text', 'verbal', 'alt', 'cue', 'question', 'change', 'chunk')"


def upgrade() -> None:
    op.add_column("memory_keys", sa.Column("position", sa.Integer(), nullable=True))
    # Widen a CHECK: the constraint is replaced, nothing a running release reads is dropped.
    op.execute("ALTER TABLE memory_keys DROP CONSTRAINT IF EXISTS ck_memory_keys_key_kind")
    op.create_check_constraint(op.f("ck_memory_keys_key_kind"), "memory_keys", KEYS_AFTER)

    op.create_table(
        "link_sources",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("fetch_status", sa.String(length=16), nullable=False),
        sa.Column("fetch_reason", sa.Text(), nullable=True),
        sa.Column("site", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("final_host", sa.Text(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("bytes_read", sa.Integer(), nullable=True),
        sa.Column("redirects", sa.Integer(), nullable=True),
        sa.Column("extraction_method", sa.String(length=32), nullable=True),
        sa.Column("channel", sa.Text(), nullable=True),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", pg.JSONB(), server_default="{}", nullable=False),
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
            "kind IN ('article', 'video', 'link')", name=op.f("ck_link_sources_kind")
        ),
        sa.CheckConstraint(
            "fetch_status IN ('pending', 'full', 'partial', 'failed', 'refused')",
            name=op.f("ck_link_sources_fetch_status"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id", "workspace_id"],
            ["memory_items.id", "memory_items.workspace_id"],
            name=op.f("fk_link_sources_item_id_workspace_id_memory_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id", "workspace_id"],
            ["turns.id", "turns.workspace_id"],
            name=op.f("fk_link_sources_turn_id_workspace_id_turns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_link_sources")),
        sa.UniqueConstraint(
            "workspace_id", "item_id", name=op.f("uq_link_sources_workspace_id_item_id")
        ),
    )
    op.create_index(
        "ix_link_sources_workspace_id_canonical_url",
        "link_sources",
        ["workspace_id", "canonical_url"],
        unique=False,
    )
    op.execute("ALTER TABLE link_sources ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY workspace_isolation ON link_sources USING ({PREDICATE}) "
        f"WITH CHECK ({PREDICATE})"
    )


def downgrade() -> None:
    op.drop_index("ix_link_sources_workspace_id_canonical_url", table_name="link_sources")
    op.drop_table("link_sources")
    op.execute("DELETE FROM memory_keys WHERE key_kind = 'chunk'")
    op.execute("ALTER TABLE memory_keys DROP CONSTRAINT IF EXISTS ck_memory_keys_key_kind")
    op.create_check_constraint(op.f("ck_memory_keys_key_kind"), "memory_keys", KEYS_BEFORE)
    op.drop_column("memory_keys", "position")

"""Identity, turns, turn events and the usage ledger, with workspace RLS.

Revision ID: 0001
Revises:
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_GROUP_ROLE = "secondmind_rw"
PREDICATE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
WORKSPACE_OWNED = ("turns", "turn_events", "usage_ledger")


def _ts(name: str, *, nullable: bool = False, default: bool = False) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default else None,
    )


def upgrade() -> None:
    op.execute(
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_GROUP_ROLE}') "
        f"THEN CREATE ROLE {APP_GROUP_ROLE} NOLOGIN; END IF; END $$"
    )

    op.create_table(
        "users",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)

    op.create_table(
        "workspaces",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        _ts("created_at", default=True),
        sa.CheckConstraint(
            "kind IN ('private', 'guest', 'persona_copy')", name="ck_workspaces_kind"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_workspaces_owner_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workspaces"),
    )
    op.create_index("ix_workspaces_owner_user_id", "workspaces", ["owner_user_id"])
    op.create_index(
        "uq_workspaces_owner_private",
        "workspaces",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'private'"),
    )

    op.create_table(
        "turns",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        _ts("started_at"),
        _ts("finished_at", nullable=True),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("prompt_versions", pg.JSONB(), server_default="[]", nullable=False),
        sa.Column("models", pg.JSONB(), server_default="{}", nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Numeric(18, 8), server_default="0", nullable=False),
        sa.Column("trace_status", sa.String(16), server_default="disabled", nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("event_count", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("status IN ('running', 'completed', 'failed')", name="ck_turns_status"),
        sa.CheckConstraint(
            "trace_status IN ('recorded', 'unavailable', 'disabled')",
            name="ck_turns_trace_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_turns_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_turns_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_turns"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_turns_id_workspace_id"),
    )
    op.create_index("ix_turns_workspace_id_id", "turns", ["workspace_id", "id"])

    op.create_table(
        "turn_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("v", sa.Integer(), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        _ts("created_at", default=True),
        sa.ForeignKeyConstraint(
            ["turn_id", "workspace_id"],
            ["turns.id", "turns.workspace_id"],
            name="fk_turn_events_turn_id_workspace_id_turns",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_turn_events"),
        sa.UniqueConstraint("turn_id", "seq", name="uq_turn_events_turn_id_seq"),
    )
    op.create_index("ix_turn_events_workspace_id", "turn_events", ["workspace_id"])

    op.create_table(
        "usage_ledger",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("step", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("price_version", sa.String(32), nullable=False),
        _ts("created_at", default=True),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_usage_ledger_owner_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id", "workspace_id"],
            ["turns.id", "turns.workspace_id"],
            name="fk_usage_ledger_turn_id_workspace_id_turns",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_usage_ledger"),
    )
    op.create_index(
        "ix_usage_ledger_owner_user_id_created_at", "usage_ledger", ["owner_user_id", "created_at"]
    )
    op.create_index("ix_usage_ledger_turn_id", "usage_ledger", ["turn_id"])
    op.create_index("ix_usage_ledger_workspace_id", "usage_ledger", ["workspace_id"])

    # Row-level security: the app role sees only rows of the workspace set for the current
    # transaction. No setting => NULL => no rows.
    for table in WORKSPACE_OWNED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY workspace_isolation ON {table} "
            f"USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
        )

    # Privileges for the app group role; login roles are members (see memory.adapters.bootstrap).
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_GROUP_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON users, workspaces, turns, turn_events, "
        f"usage_ledger TO {APP_GROUP_ROLE}"
    )
    op.execute(f"GRANT SELECT ON alembic_version TO {APP_GROUP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_GROUP_ROLE}")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_GROUP_ROLE}"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {APP_GROUP_ROLE}"
    )


def downgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_GROUP_ROLE}"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE USAGE, SELECT ON SEQUENCES FROM {APP_GROUP_ROLE}"
    )
    for table in ("usage_ledger", "turn_events", "turns", "workspaces", "users"):
        op.drop_table(table)

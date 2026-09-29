"""Spend safety (R.10, ADR-0032): a tier per user, audited in ``tier_changes``; ledger rows
marked ``system`` when the app pays for them (background indexing, housekeeping) rather than
the person; and ``global_spend``, the app's total spend since a moment, for the spend counters
to reconcile from without reading any workspace's rows.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_GROUP_ROLE = "secondmind_rw"
TIERS = "tier IN ('guest', 'standard', 'premium')"


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("tier", sa.String(16), nullable=False, server_default="standard")
    )
    op.create_check_constraint(op.f("ck_users_tier"), "users", TIERS)

    op.create_table(
        "tier_changes",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("from_tier", sa.String(16), nullable=False),
        sa.Column("to_tier", sa.String(16), nullable=False),
        sa.Column("changed_by", sa.Text(), nullable=False),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_tier_changes_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tier_changes"),
    )
    op.create_index("ix_tier_changes_user_id", "tier_changes", ["user_id"])
    # The audit is written by the admin CLI as the owner; the app may read it, never change it.
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON tier_changes FROM {APP_GROUP_ROLE}")
    op.execute(f"GRANT SELECT ON tier_changes TO {APP_GROUP_ROLE}")

    op.add_column(
        "usage_ledger",
        sa.Column("system", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_usage_ledger_created_at", "usage_ledger", ["created_at"])

    # Totals only, across every workspace, for the spend counters (the app role can't read
    # other workspaces' rows, and this returns none of them).
    op.execute(
        """
        CREATE FUNCTION global_spend(since timestamptz)
        RETURNS TABLE (provider text, cost_usd numeric)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT provider::text, coalesce(sum(cost_usd), 0)
            FROM usage_ledger WHERE created_at >= since GROUP BY provider
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION global_spend(timestamptz) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION global_spend(timestamptz) TO {APP_GROUP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS global_spend(timestamptz)")
    op.drop_index("ix_usage_ledger_created_at", table_name="usage_ledger")
    op.drop_column("usage_ledger", "system")
    op.drop_index("ix_tier_changes_user_id", table_name="tier_changes")
    op.drop_table("tier_changes")
    op.drop_constraint(op.f("ck_users_tier"), "users", type_="check")
    op.drop_column("users", "tier")

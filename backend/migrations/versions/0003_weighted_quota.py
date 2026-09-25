"""Weighted quota: every ledger row and turn records the quota tokens it was charged, its tokens
at the model's weight against the baseline (ADR-0030). Rows from before weighting are
backfilled at weight 1, so the quota already spent doesn't change.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RAW = "input_tokens + cached_input_tokens + output_tokens"


def upgrade() -> None:
    op.add_column(
        "usage_ledger",
        sa.Column("charged_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(f"UPDATE usage_ledger SET charged_tokens = {_RAW}")
    # New rows always say what they were charged.
    op.alter_column("usage_ledger", "charged_tokens", server_default=None)

    op.add_column(
        "turns",
        sa.Column("charged_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(f"UPDATE turns SET charged_tokens = {_RAW}")


def downgrade() -> None:
    op.drop_column("turns", "charged_tokens")
    op.drop_column("usage_ledger", "charged_tokens")

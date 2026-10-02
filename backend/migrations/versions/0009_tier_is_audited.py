"""A person's tier changes only through the audited path (ADR-0032).

``users.tier`` sets a person's quota and model picker, and ``tier_changes`` records who changed it
and when. That record is written by the admin CLI as the owner, but the app role could still
``UPDATE users SET tier = ...`` directly (a bug, an injection) and leave no row. The app inserts
and deletes users (guests come and go) and never updates one, so ``UPDATE`` is taken away; nothing
a running release does needs it, and a release that wants to change another column grants that
column by name, never ``tier``.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_GROUP_ROLE = "secondmind_rw"


def upgrade() -> None:
    op.execute(f"REVOKE UPDATE ON users FROM {APP_GROUP_ROLE}")


def downgrade() -> None:
    op.execute(f"GRANT UPDATE ON users TO {APP_GROUP_ROLE}")

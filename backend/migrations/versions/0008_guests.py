"""Guests (S4.12, ADR-0039): what a guest's expiry and their share of the daily cap need.

A workspace can be marked ``expired_at`` (its content is emptied, the row stays); a guest has at
most one scratch workspace. ``usage_ledger.turn_id`` may become NULL when its turn is deleted, so
the costs (which carry no content) outlive the content and the caps and totals stay right.
``guest_spend`` is the guests' total cost since a moment (a total, no rows), and
``expire_guest_workspaces`` empties the workspaces of guests older than a moment; both are
``SECURITY DEFINER`` because the app role sees only one workspace at a time.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_GROUP_ROLE = "secondmind_rw"

GUEST_SPEND = """
CREATE FUNCTION guest_spend(since timestamptz) RETURNS numeric
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT coalesce(sum(l.cost_usd), 0)
    FROM usage_ledger l JOIN users u ON u.id = l.owner_user_id
    WHERE u.tier = 'guest' AND l.created_at >= since
$$
"""

EXPIRE = """
CREATE FUNCTION expire_guest_workspaces(p_before timestamptz) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    v_ids uuid[];
BEGIN
    SELECT array_agg(w.id) INTO v_ids
    FROM workspaces w JOIN users u ON u.id = w.owner_user_id
    WHERE u.tier = 'guest' AND u.created_at < p_before AND w.expired_at IS NULL
      AND w.kind IN ('persona_copy', 'scratch', 'guest');
    IF v_ids IS NULL THEN
        RETURN 0;
    END IF;
    -- Everything with content, children before parents. usage_ledger is kept: costs only.
    DELETE FROM held_writes WHERE workspace_id = ANY(v_ids);
    DELETE FROM item_access WHERE workspace_id = ANY(v_ids);
    DELETE FROM write_log WHERE workspace_id = ANY(v_ids);
    DELETE FROM item_versions WHERE workspace_id = ANY(v_ids);
    DELETE FROM link_sources WHERE workspace_id = ANY(v_ids);
    DELETE FROM memory_keys WHERE workspace_id = ANY(v_ids);
    DELETE FROM memory_links WHERE workspace_id = ANY(v_ids);
    DELETE FROM memory_entities WHERE workspace_id = ANY(v_ids);
    DELETE FROM triggers WHERE workspace_id = ANY(v_ids);
    DELETE FROM entity_relations WHERE workspace_id = ANY(v_ids);
    DELETE FROM memory_items WHERE workspace_id = ANY(v_ids);
    DELETE FROM conversation_keys WHERE workspace_id = ANY(v_ids);
    DELETE FROM turn_events WHERE workspace_id = ANY(v_ids);
    DELETE FROM turns WHERE workspace_id = ANY(v_ids);
    DELETE FROM entities WHERE workspace_id = ANY(v_ids) AND kind <> 'self';
    UPDATE entities SET aliases = '[]', labels = '[]', attributes = '{}', summary = NULL
        WHERE workspace_id = ANY(v_ids) AND kind = 'self';
    DELETE FROM categories WHERE workspace_id = ANY(v_ids);
    DELETE FROM vocab_terms WHERE workspace_id = ANY(v_ids);
    UPDATE workspaces SET expired_at = now() WHERE id = ANY(v_ids);
    RETURN array_length(v_ids, 1);
END $$
"""


def upgrade() -> None:
    op.add_column("workspaces", sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "uq_workspaces_owner_scratch",
        "workspaces",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'scratch'"),
    )
    # The cost of a call outlives the turn it was for: when the turn is deleted (a guest's content
    # expires) the row stays, with no turn.
    op.alter_column("usage_ledger", "turn_id", nullable=True)
    op.execute(
        "ALTER TABLE usage_ledger DROP CONSTRAINT fk_usage_ledger_turn_id_workspace_id_turns"
    )
    op.execute(
        "ALTER TABLE usage_ledger ADD CONSTRAINT fk_usage_ledger_turn_id_workspace_id_turns "
        "FOREIGN KEY (turn_id, workspace_id) REFERENCES turns (id, workspace_id) "
        "ON DELETE SET NULL (turn_id)"
    )
    op.execute(GUEST_SPEND)
    op.execute(EXPIRE)
    for fn in ("guest_spend(timestamptz)", "expire_guest_workspaces(timestamptz)"):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO {APP_GROUP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS expire_guest_workspaces(timestamptz)")
    op.execute("DROP FUNCTION IF EXISTS guest_spend(timestamptz)")
    op.execute("DELETE FROM usage_ledger WHERE turn_id IS NULL")
    op.execute(
        "ALTER TABLE usage_ledger DROP CONSTRAINT fk_usage_ledger_turn_id_workspace_id_turns"
    )
    op.execute(
        "ALTER TABLE usage_ledger ADD CONSTRAINT fk_usage_ledger_turn_id_workspace_id_turns "
        "FOREIGN KEY (turn_id, workspace_id) REFERENCES turns (id, workspace_id) ON DELETE CASCADE"
    )
    op.alter_column("usage_ledger", "turn_id", nullable=False)
    op.drop_index("uq_workspaces_owner_scratch", table_name="workspaces")
    op.drop_column("workspaces", "expired_at")

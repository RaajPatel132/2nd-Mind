"""S1.6 / NFR-2.2: nothing crosses workspaces, for every workspace-owned table.

Rows written in workspace A cannot be read, updated or deleted from workspace B, through the
repositories *and* through raw SQL on the app role. A missing workspace setting means zero
rows. Extend TABLES whenever a workspace-owned table is added (a catalog test enforces RLS).
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from secondmind.agent.adapters import SqlTurnStore
from secondmind.auth.adapters import SqlIdentityStore
from secondmind.core import (
    IntentEvent,
    ModelCallEvent,
    NotFoundError,
    Usage,
    WorkspaceScope,
    new_id,
    utc_now,
)
from secondmind.memory.adapters import Database

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class Seeded:
    scope_a: WorkspaceScope
    scope_b: WorkspaceScope
    turn_a: uuid.UUID
    turn_b: uuid.UUID


@dataclass(frozen=True)
class TableSpec:
    name: str
    key: str  # column identifying rows written for workspace A's turn
    update: str  # harmless column assignment used to probe UPDATE


TABLES = [
    TableSpec("turns", "id", "output = 'tampered'"),
    TableSpec("turn_events", "turn_id", "type = 'tampered'"),
    TableSpec("usage_ledger", "turn_id", "step = 'tampered'"),
]


def _model_call() -> ModelCallEvent:
    usage = Usage(
        provider="fake",
        model="fake-chat",
        input_tokens=10,
        output_tokens=5,
        cost_usd=Decimal("0.00003500"),
        latency_ms=12,
        price_version="test",
    )
    return ModelCallEvent(
        step="answer",
        provider="fake",
        model="fake-chat",
        started_at=utc_now(),
        latency_ms=12,
        attempts=1,
        usage=usage,
    )


async def _workspace(identity: SqlIdentityStore) -> WorkspaceScope:
    user, ws = await identity.ensure_user_with_private_workspace(
        email=f"{new_id()}@example.test", timezone="UTC"
    )
    return WorkspaceScope(workspace_id=ws.id, user_id=user.id)


async def _turn_with_everything(db: Database, scope: WorkspaceScope) -> uuid.UUID:
    store = SqlTurnStore(db, scope)
    turn = await store.create(
        turn_id=new_id(), text="synthetic message", config_hash="0" * 64, started_at=utc_now()
    )
    await store.append(
        turn.id, IntentEvent(intent="chit_chat", confidence=1.0, reason="stub", source="stub")
    )
    await store.record_model_call(turn.id, _model_call())
    return turn.id


@pytest.fixture
async def seeded(app_db: Database, identity: SqlIdentityStore) -> Seeded:
    scope_a, scope_b = await _workspace(identity), await _workspace(identity)
    return Seeded(
        scope_a=scope_a,
        scope_b=scope_b,
        turn_a=await _turn_with_everything(app_db, scope_a),
        turn_b=await _turn_with_everything(app_db, scope_b),
    )


async def _count(
    db: Database, table: TableSpec, ids: uuid.UUID, scope: WorkspaceScope | None
) -> int:
    sql = text(f"SELECT count(*) FROM {table.name} WHERE {table.key} = :id")  # noqa: S608
    if scope is None:
        async with db.identity() as session:
            return int((await session.execute(sql, {"id": ids})).scalar_one())
    async with db.workspace(scope) as session:
        return int((await session.execute(sql, {"id": ids})).scalar_one())


# ------------------------------------------------------------------ through the repositories


async def test_repository_cannot_read_another_workspaces_turn(
    app_db: Database, seeded: Seeded
) -> None:
    store_b = SqlTurnStore(app_db, seeded.scope_b)
    assert await store_b.get(seeded.turn_a) is None
    assert await store_b.events(seeded.turn_a) == []
    assert seeded.turn_a not in {t.id for t in await store_b.recent(limit=100)}


async def test_repository_cannot_write_to_another_workspaces_turn(
    app_db: Database, seeded: Seeded
) -> None:
    store_b = SqlTurnStore(app_db, seeded.scope_b)
    with pytest.raises(NotFoundError):
        await store_b.append(
            seeded.turn_a, IntentEvent(intent="chit_chat", confidence=1, reason="x", source="stub")
        )
    store_a = SqlTurnStore(app_db, seeded.scope_a)
    events = await store_a.events(seeded.turn_a)
    assert [e.seq for e in events] == [1, 2]


# ------------------------------------------------------------------ through raw SQL (app role)


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
async def test_raw_sql_read_is_scoped(app_db: Database, seeded: Seeded, table: TableSpec) -> None:
    assert await _count(app_db, table, seeded.turn_a, seeded.scope_a) >= 1
    assert await _count(app_db, table, seeded.turn_a, seeded.scope_b) == 0


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
async def test_missing_workspace_setting_means_zero_rows(
    app_db: Database, seeded: Seeded, table: TableSpec
) -> None:
    assert await _count(app_db, table, seeded.turn_a, None) == 0
    async with app_db.identity() as session:
        total = (await session.execute(text(f"SELECT count(*) FROM {table.name}"))).scalar_one()  # noqa: S608
        assert total == 0
        # An explicitly empty setting behaves the same (no error, no rows).
        await session.execute(text("SELECT set_config('app.workspace_id', '', true)"))
        total = (await session.execute(text(f"SELECT count(*) FROM {table.name}"))).scalar_one()  # noqa: S608
        assert total == 0


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
async def test_raw_sql_update_and_delete_cannot_touch_other_workspaces(
    app_db: Database, owner_db: Database, seeded: Seeded, table: TableSpec
) -> None:
    async with app_db.workspace(seeded.scope_b) as session:
        updated = await session.execute(
            text(f"UPDATE {table.name} SET {table.update} WHERE {table.key} = :id"),  # noqa: S608
            {"id": seeded.turn_a},
        )
        deleted = await session.execute(
            text(f"DELETE FROM {table.name} WHERE {table.key} = :id"),  # noqa: S608
            {"id": seeded.turn_a},
        )
        assert updated.rowcount == 0  # type: ignore[attr-defined]
        assert deleted.rowcount == 0  # type: ignore[attr-defined]
    # The owner (not subject to RLS) confirms A's rows are intact.
    async with owner_db.identity() as session:
        remaining = (
            await session.execute(
                text(
                    f"SELECT count(*) FROM {table.name} WHERE {table.key} = :id "  # noqa: S608
                    "AND workspace_id = :ws"
                ),
                {"id": seeded.turn_a, "ws": seeded.scope_a.workspace_id},
            )
        ).scalar_one()
    assert remaining >= 1


async def test_raw_sql_cannot_insert_rows_into_another_workspace(
    app_db: Database, seeded: Seeded
) -> None:
    """WITH CHECK: from workspace B, a row claiming workspace A is rejected."""
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_db.workspace(seeded.scope_b) as session:
            await session.execute(
                text(
                    "INSERT INTO turns (id, workspace_id, user_id, input, status, started_at, "
                    "config_hash) VALUES (:id, :ws, :uid, 'x', 'running', now(), 'h')"
                ),
                {"id": new_id(), "ws": seeded.scope_a.workspace_id, "uid": seeded.scope_b.user_id},
            )


async def test_raw_sql_cannot_move_rows_into_another_workspace(
    app_db: Database, seeded: Seeded
) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_db.workspace(seeded.scope_b) as session:
            await session.execute(
                text("UPDATE turns SET workspace_id = :ws WHERE id = :id"),
                {"ws": seeded.scope_a.workspace_id, "id": seeded.turn_b},
            )


async def test_ledger_is_charged_to_the_workspace_owner(app_db: Database, seeded: Seeded) -> None:
    async with app_db.workspace(seeded.scope_a) as session:
        owner = (
            await session.execute(
                text("SELECT owner_user_id FROM usage_ledger WHERE turn_id = :t"),
                {"t": seeded.turn_a},
            )
        ).scalar_one()
    assert owner == seeded.scope_a.user_id

"""S1.6 / S2.1 / NFR-2.2: nothing crosses workspaces, for every workspace-owned table.

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
    Intent,
    IntentEvent,
    ModelCallEvent,
    NotFoundError,
    Usage,
    WorkspaceScope,
    new_id,
    utc_now,
)
from secondmind.memory import Memory, UpdateItem
from secondmind.memory.adapters import Database, sql_memory
from tests.integration.memory_seed import (
    EMBED_MODEL,
    SeededMemory,
    real_turn,
    seed_memory,
    vector,
    workspace,
)

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class Seeded:
    scope_a: WorkspaceScope
    scope_b: WorkspaceScope
    turn_a: uuid.UUID
    turn_b: uuid.UUID
    memory_a: SeededMemory
    memory_b: SeededMemory


@dataclass(frozen=True)
class TableSpec:
    name: str
    update: str  # harmless column assignment used to probe UPDATE


TABLES = [
    TableSpec("turns", "output = 'tampered'"),
    TableSpec("turn_events", "type = 'tampered'"),
    TableSpec("usage_ledger", "step = 'tampered'"),
    TableSpec("entities", "name = 'tampered'"),
    TableSpec("categories", "display_name = 'tampered'"),
    TableSpec("vocab_terms", "aliases = '[]'::jsonb"),
    TableSpec("memory_items", "text = 'tampered'"),
    TableSpec("memory_entities", "role = role"),
    TableSpec("memory_links", "link_type = link_type"),
    TableSpec("entity_relations", "relation = 'tampered'"),
    TableSpec("memory_keys", "text = 'tampered'"),
    TableSpec("triggers", "state = state"),
    TableSpec("item_versions", "version = version"),
    TableSpec("write_log", "rationale = 'tampered'"),
    TableSpec("held_writes", "status = status"),
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


async def _turn_with_everything(db: Database, scope: WorkspaceScope) -> uuid.UUID:
    store = SqlTurnStore(db, scope)
    turn = await store.create(
        turn_id=new_id(), text="synthetic message", config_hash="0" * 64, started_at=utc_now()
    )
    await store.append(
        turn.id, IntentEvent(intent=Intent.CHIT_CHAT, confidence=1.0, reason="stub", source="stub")
    )
    await store.record_model_call(turn.id, _model_call())
    return turn.id


@pytest.fixture
async def seeded(app_db: Database, identity: SqlIdentityStore) -> Seeded:
    scope_a, scope_b = await workspace(identity), await workspace(identity)
    return Seeded(
        scope_a=scope_a,
        scope_b=scope_b,
        turn_a=await _turn_with_everything(app_db, scope_a),
        turn_b=await _turn_with_everything(app_db, scope_b),
        memory_a=await seed_memory(app_db, scope_a),
        memory_b=await seed_memory(app_db, scope_b),
    )


async def _count(
    db: Database, table: TableSpec, workspace_id: uuid.UUID, scope: WorkspaceScope | None
) -> int:
    """Rows of ``workspace_id`` in ``table`` as seen from ``scope`` (None: no setting)."""
    sql = text(f"SELECT count(*) FROM {table.name} WHERE workspace_id = :ws")  # noqa: S608
    if scope is None:
        async with db.identity() as session:
            return int((await session.execute(sql, {"ws": workspace_id})).scalar_one())
    async with db.workspace(scope) as session:
        return int((await session.execute(sql, {"ws": workspace_id})).scalar_one())


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
            seeded.turn_a,
            IntentEvent(intent=Intent.CHIT_CHAT, confidence=1, reason="x", source="stub"),
        )
    store_a = SqlTurnStore(app_db, seeded.scope_a)
    events = await store_a.events(seeded.turn_a)
    assert [e.seq for e in events] == [1, 2]


# ------------------------------------------------------------------ through raw SQL (app role)


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
async def test_raw_sql_read_is_scoped(app_db: Database, seeded: Seeded, table: TableSpec) -> None:
    ws_a = seeded.scope_a.workspace_id
    assert await _count(app_db, table, ws_a, seeded.scope_a) >= 1
    assert await _count(app_db, table, ws_a, seeded.scope_b) == 0


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
async def test_missing_workspace_setting_means_zero_rows(
    app_db: Database, seeded: Seeded, table: TableSpec
) -> None:
    assert await _count(app_db, table, seeded.scope_a.workspace_id, None) == 0
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
        ws_a = {"ws": seeded.scope_a.workspace_id}
        updated = await session.execute(
            text(f"UPDATE {table.name} SET {table.update} WHERE workspace_id = :ws"),  # noqa: S608
            ws_a,
        )
        deleted = await session.execute(
            text(f"DELETE FROM {table.name} WHERE workspace_id = :ws"),  # noqa: S608
            ws_a,
        )
        assert updated.rowcount == 0  # type: ignore[attr-defined]
        assert deleted.rowcount == 0  # type: ignore[attr-defined]
    # The owner (not subject to RLS) confirms A's rows are intact.
    async with owner_db.identity() as session:
        remaining = (
            await session.execute(
                text(f"SELECT count(*) FROM {table.name} WHERE workspace_id = :ws"),  # noqa: S608
                {"ws": seeded.scope_a.workspace_id},
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


# ------------------------------------------------------------------ memory (S2.1)


async def test_memory_repositories_cannot_see_another_workspace(
    app_db: Database, seeded: Seeded
) -> None:
    reader_b = Memory(sql_memory(app_db)).reader(seeded.scope_b)
    a = seeded.memory_a
    assert await reader_b.item(a.item_id) is None
    assert await reader_b.entity(a.person_id) is None
    assert await reader_b.held_write(a.held_id) is None
    assert await reader_b.write_log(a.turn.turn_id) == []
    assert await reader_b.keys([a.item_id]) == []
    assert await reader_b.triggers([a.task_id]) == []
    assert (await reader_b.self_entity()).workspace_id == seeded.scope_b.workspace_id
    seen = {i.id for i in await reader_b.find_items(limit=500)}
    assert a.item_id not in seen
    assert seeded.memory_b.item_id in seen


async def test_memory_writes_cannot_reach_another_workspace(
    app_db: Database, seeded: Seeded
) -> None:
    memory = Memory(sql_memory(app_db))
    a = seeded.memory_a
    turn_b = await real_turn(app_db, seeded.scope_b)
    writer = memory.writer(seeded.scope_b, turn_b)
    writer.add(UpdateItem(item_id=a.item_id, changes={"text": "tampered"}, title="tamper"))
    result = await writer.commit()
    assert all(e.op in ("not_written", "conflict") for e in result.diff.entries)

    undo_b = await real_turn(app_db, seeded.scope_b)
    undone = await memory.undo(seeded.scope_b, undo_b, a.turn.turn_id)
    assert [e.op for e in undone.diff.entries] == ["not_written"]

    with pytest.raises(NotFoundError):
        await memory.confirm_held(
            seeded.scope_b, await real_turn(app_db, seeded.scope_b), a.held_id
        )

    stored = await memory.reader(seeded.scope_a).item(a.item_id)
    assert stored is not None
    assert stored.text == "Nisha likes tulips"


async def test_raw_vector_search_from_another_workspace_finds_nothing(
    app_db: Database, seeded: Seeded
) -> None:
    """The nearest-neighbour scan itself is RLS-scoped, not just the item lookup after it."""
    probe = "[" + ",".join(f"{v:.7g}" for v in vector("Nisha likes tulips")) + "]"
    sql = text(
        "SELECT k.workspace_id, 1 - (k.embedding <=> CAST(:q AS vector)) AS score "
        "FROM memory_keys k WHERE k.embedding IS NOT NULL AND k.embedding_model = :m "
        "ORDER BY k.embedding <=> CAST(:q AS vector) LIMIT 50"
    )
    async with app_db.workspace(seeded.scope_b) as session:
        rows = (await session.execute(sql, {"q": probe, "m": EMBED_MODEL})).all()
    assert rows
    assert {r[0] for r in rows} == {seeded.scope_b.workspace_id}
    async with app_db.identity() as session:
        assert (await session.execute(sql, {"q": probe, "m": EMBED_MODEL})).all() == []


async def test_raw_text_search_from_another_workspace_finds_nothing(
    app_db: Database, seeded: Seeded
) -> None:
    sql = text(
        "SELECT DISTINCT workspace_id FROM memory_keys "
        "WHERE tsv @@ plainto_tsquery('english', 'tulips')"
    )
    async with app_db.workspace(seeded.scope_b) as session:
        found = set((await session.execute(sql)).scalars())
    assert found == {seeded.scope_b.workspace_id}
    async with app_db.identity() as session:
        assert list((await session.execute(sql)).scalars()) == []

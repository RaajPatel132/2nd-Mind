"""S2.1 / S2.2 / S2.9 / S2.12 on real Postgres: the writer, undo, held writes and keys work through
``SqlMemoryStore`` under RLS exactly as they do on the in-memory store."""

from typing import Any

import pytest
from sqlalchemy import text

from secondmind.agent import TurnKind
from secondmind.auth.adapters import SqlIdentityStore
from secondmind.core import (
    EntityRole,
    ItemStatus,
    KeyKind,
    Kind,
    TargetType,
    WorkspaceScope,
    WriteOp,
    initial_state,
    new_id,
)
from secondmind.memory import Memory, SupersedeItem
from secondmind.memory.adapters import EMBED_DIMENSIONS, Database, sql_memory
from secondmind.memory.adapters.store import SqlMemoryTx
from tests.integration.memory_seed import (
    EMBED_MODEL,
    FakeEmbedder,
    real_turn,
    seed_memory,
    vector,
    workspace,
)
from tests.unit.memory.helpers import create, item, person

pytestmark = pytest.mark.integration


@pytest.fixture
async def ws(identity: SqlIdentityStore) -> WorkspaceScope:
    return await workspace(identity)


@pytest.fixture
def memory(app_db: Database) -> Memory:
    return Memory(sql_memory(app_db))


async def test_a_new_workspace_has_its_self_entity(memory: Memory, ws: WorkspaceScope) -> None:
    me = await memory.reader(ws).self_entity()
    assert me.name == "me"
    assert me.workspace_id == ws.workspace_id


async def test_a_turn_round_trips_through_every_memory_table(
    app_db: Database, memory: Memory, ws: WorkspaceScope
) -> None:
    seeded = await seed_memory(app_db, ws)
    reader = memory.reader(ws)

    tulips = await reader.item(seeded.item_id)
    assert tulips is not None
    assert (tulips.text, tulips.kind) == ("Nisha likes tulips", Kind.PREFERENCE)
    assert tulips.state == initial_state(Kind.PREFERENCE)
    assert tulips.created_by_turn_id == seeded.turn.turn_id
    assert tulips.mentioned_at.tzinfo is not None
    [category] = await reader.categories()
    assert (category.slug, tulips.category_id) == ("gifts", category.id)

    roles = await reader.item_entities([seeded.item_id, seeded.task_id])
    assert {(r.item_id, r.entity_id, r.role) for r in roles} == {
        (seeded.item_id, seeded.person_id, EntityRole.ABOUT),
        (seeded.task_id, seeded.person_id, EntityRole.ABOUT),
    }
    [trigger] = await reader.triggers([seeded.task_id])
    assert trigger.fires_at is not None
    [relation] = await reader.relations([seeded.person_id])
    assert relation.relation == "sibling"
    assert [(v.slug, v.aliases) for v in await reader.vocab()] == [("likes", ["enjoys"])]

    log = await reader.write_log(seeded.turn.turn_id)
    assert [r.seq for r in log] == sorted(r.seq for r in log)
    assert (WriteOp.CREATE, TargetType.ITEM) in {(r.op, r.target_type) for r in log}
    created = next(r for r in log if r.target_id == seeded.item_id and r.op is WriteOp.CREATE)
    assert created.after is not None
    assert created.after["text"] == "Nisha likes tulips"  # JSONB snapshot survives the trip

    [held] = await reader.held_writes(status="pending")
    assert held.id == seeded.held_id
    assert held.ops[0]["type"] == "update_item"


async def test_a_failure_halfway_through_a_multi_item_save_persists_nothing(
    app_db: Database, memory: Memory, ws: WorkspaceScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR-6.1 on the real database: one transaction per turn, not one per op."""
    real_insert = SqlMemoryTx.insert_item
    calls = 0

    async def insert_item(self: SqlMemoryTx, record: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected failure on the second item")
        await real_insert(self, record)

    monkeypatch.setattr(SqlMemoryTx, "insert_item", insert_item)
    turn = await real_turn(app_db, ws)
    kabir = person("Kabir", "partner")
    first = create(item("first thing"), (kabir.entity_id, EntityRole.ABOUT))
    second = create(item("second thing"))
    writer = memory.writer(ws, turn)
    writer.add(kabir, first, second)

    with pytest.raises(RuntimeError, match="second item"):
        await writer.commit()

    reader = memory.reader(ws)
    assert await reader.items([first.item_id, second.item_id]) == []
    assert await reader.entity(kabir.entity_id) is None
    assert await reader.write_log(turn.turn_id) == []


async def test_supersede_then_undo_then_redo(
    app_db: Database, memory: Memory, ws: WorkspaceScope
) -> None:
    reader = memory.reader(ws)
    old = create(item("I live in Bengaluru", Kind.FACT, predicate="lives_in"))
    first = memory.writer(ws, await real_turn(app_db, ws))
    first.add(old)
    await first.commit()

    moved = await real_turn(app_db, ws)
    new = create(item("I live in Pune", Kind.FACT, predicate="lives_in"))
    second = memory.writer(ws, moved)
    second.add(
        new,
        SupersedeItem(old_id=old.item_id, new_id=new.item_id, link_id=new_id(), valid_to=moved.now),
    )
    await second.commit()
    current = await reader.find_items(kinds=[Kind.FACT], predicate="lives_in", current_only=True)
    assert [i.text for i in current] == ["I live in Pune"]

    undo = await real_turn(app_db, ws, TurnKind.UNDO, parent=moved.turn_id)
    result = await memory.undo(ws, undo, moved.turn_id)
    assert result.diff.undo_of == moved.turn_id
    current = await reader.find_items(kinds=[Kind.FACT], predicate="lives_in", current_only=True)
    assert [i.text for i in current] == ["I live in Bengaluru"]
    reopened = await reader.item(old.item_id)
    assert reopened is not None
    assert (reopened.state, reopened.valid_to) == (initial_state(Kind.FACT), None)
    gone = await reader.item(new.item_id)
    assert gone is not None
    assert gone.status is ItemStatus.DELETED

    redo = await real_turn(app_db, ws, TurnKind.UNDO, parent=undo.turn_id)
    await memory.undo(ws, redo, undo.turn_id)
    current = await reader.find_items(kinds=[Kind.FACT], predicate="lives_in", current_only=True)
    assert [i.text for i in current] == ["I live in Pune"]


async def test_a_held_write_is_confirmed_from_its_stored_ops(
    app_db: Database, memory: Memory, ws: WorkspaceScope
) -> None:
    seeded = await seed_memory(app_db, ws)
    confirm = await real_turn(app_db, ws, TurnKind.CONFIRM, parent=seeded.turn.turn_id)

    result = await memory.confirm_held(ws, confirm, seeded.held_id)

    assert [e.op for e in result.diff.entries] == ["updated"]
    reader = memory.reader(ws)
    held = await reader.held_write(seeded.held_id)
    assert held is not None
    assert (held.status, held.resolved_turn_id) == ("confirmed", confirm.turn_id)
    core = [i.text for i in await reader.find_items(in_core=True)]
    assert core == ["I see a therapist on Tuesdays"]


async def test_keys_store_vectors_and_text_search_and_reuse_embeddings_by_hash(
    app_db: Database, memory: Memory, ws: WorkspaceScope
) -> None:
    seeded = await seed_memory(app_db, ws)
    reader = memory.reader(ws)
    keys = await reader.keys([seeded.item_id])
    assert {k.key_kind for k in keys} >= {KeyKind.TEXT, KeyKind.VERBAL}
    text_key = next(k for k in keys if k.key_kind is KeyKind.TEXT)
    assert text_key.embedding is not None
    assert len(text_key.embedding) == EMBED_DIMENSIONS
    assert text_key.embedding_model == EMBED_MODEL

    ranked = await reader.similar_items(vector(text_key.text), limit=3, model=EMBED_MODEL)
    assert ranked[0][0] == seeded.item_id
    assert ranked[0][1] == pytest.approx(1.0, abs=1e-5)

    async with app_db.workspace(ws) as session:
        found = (
            (
                await session.execute(
                    text(
                        "SELECT DISTINCT item_id FROM memory_keys "
                        "WHERE tsv @@ plainto_tsquery('english', 'tulips')"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert set(found) == {seeded.item_id, seeded.task_id}

    embedder = FakeEmbedder()
    again = memory.keys(ws, timezone="UTC", embed=embedder, model=EMBED_MODEL)
    report = await again.rebuild([seeded.item_id])
    assert report.embedded == 0
    assert report.cache_hits >= 1
    assert embedder.calls == [[]]  # told about the hits (for the event), asked to embed nothing

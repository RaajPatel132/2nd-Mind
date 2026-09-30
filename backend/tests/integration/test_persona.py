"""S4.10 and S4.11 on Postgres: the template, its copies, and what a copy must never do."""

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from secondmind.auth import WorkspaceKind, resolve_scope
from secondmind.auth.adapters import SqlIdentityStore
from secondmind.core import KeyKind, NotFoundError, WorkspaceScope
from secondmind.memory import Memory
from secondmind.memory.adapters import Database, sql_memory
from secondmind.persona.adapters import workspace_tables
from tests.integration.memory_seed import workspace
from tests.integration.persona_seed import PersonaWorld, load_template

pytestmark = pytest.mark.integration

TABLES = [
    "categories",
    "vocab_terms",
    "turns",
    "entities",
    "memory_items",
    "memory_entities",
    "memory_links",
    "entity_relations",
    "triggers",
    "memory_keys",
    "link_sources",
    "conversation_keys",
]


@pytest.fixture(scope="module")
async def world(app_db: Database, identity: SqlIdentityStore) -> PersonaWorld:
    return await load_template(app_db, identity)


async def counts(db: Database, workspace_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, int]:
    scope = WorkspaceScope(workspace_id=workspace_id, user_id=user_id)
    out: dict[str, int] = {}
    async with db.workspace(scope) as session:
        for table in TABLES:
            query = text(f"SELECT count(*) FROM {table}")  # noqa: S608 - a fixed list of tables
            out[table] = int((await session.execute(query)).scalar_one())
    return out


async def test_the_template_is_the_seed_loaded_through_the_writer(world: PersonaWorld) -> None:
    template = world.template
    assert template.kind is WorkspaceKind.TEMPLATE
    assert (template.seed_id, template.seed_version) == ("aditi-rao", 1)
    assert template.seed_hash
    user = await world.identity.get_user(template.owner_user_id)
    assert user is not None
    assert user.email is None  # a system user: no email, so nobody can sign in as it
    n = await counts(world.db, template.id, template.owner_user_id)
    assert 150 <= n["memory_items"] <= 250
    assert n["link_sources"] == 8  # three full, three partial, two videos
    assert n["turns"] == 11  # ten past chats and the turn that loaded the seed
    assert n["triggers"] >= 6
    assert n["memory_keys"] > n["memory_items"]
    assert n["conversation_keys"] >= 10
    scope = WorkspaceScope(workspace_id=template.id, user_id=template.owner_user_id)
    keys = await Memory(sql_memory(world.db)).reader(scope).keys(list(world.seeded.items.values()))
    assert all(k.embedding is not None for k in keys)
    assert any(k.key_kind.value == "chunk" for k in keys)  # a full link's passages


async def test_the_template_can_never_be_entered(
    world: PersonaWorld, app_db: Database, identity: SqlIdentityStore
) -> None:
    t = world.template
    with pytest.raises(NotFoundError):  # not even by its own owner
        await resolve_scope(identity, user_id=t.owner_user_id, workspace_id=t.id)
    assert await identity.workspaces_for(t.owner_user_id) == []
    assert t.id not in {w.id for w in await identity.all_workspaces()}
    other = await workspace(identity)
    with pytest.raises(NotFoundError):
        await resolve_scope(identity, user_id=other.user_id, workspace_id=t.id)


async def test_a_second_load_of_the_same_file_does_nothing(
    world: PersonaWorld, app_db: Database, identity: SqlIdentityStore
) -> None:
    from secondmind.agent.adapters import SqlTurnStore  # noqa: PLC0415
    from secondmind.links.adapters import SqlLinkStore  # noqa: PLC0415
    from secondmind.persona import TemplateDeps, seed_template  # noqa: PLC0415
    from secondmind.persona.adapters import SqlPersonaStore  # noqa: PLC0415
    from secondmind.retrieval.adapters import SqlConversationStore  # noqa: PLC0415

    deps = TemplateDeps(
        identity=identity,
        store=SqlPersonaStore(app_db),
        memory=Memory(sql_memory(app_db)),
        turns=lambda s: SqlTurnStore(app_db, s),
        conversation=lambda s: SqlConversationStore(app_db, s),
        sources=lambda s: SqlLinkStore(app_db, s),
        embed=None,
        embedding_model="none",
    )
    same, written = await seed_template(deps, world.seed, hash_of=world.template.seed_hash)
    assert written is None
    assert same.id == world.template.id


async def test_a_copy_has_every_row_under_new_ids_and_no_model_is_called(
    world: PersonaWorld, identity: SqlIdentityStore
) -> None:
    visitor = await workspace(identity)
    calls_before = len(world.embed_calls)
    started = time.perf_counter()
    copy = await world.service().make_copy(visitor.user_id, moved_days=0)
    seconds = time.perf_counter() - started
    assert len(world.embed_calls) == calls_before  # nothing was embedded: the vectors were copied
    assert seconds < 2.0
    assert copy.kind is WorkspaceKind.PERSONA_COPY
    assert (copy.seed_id, copy.seed_version, copy.moved_days) == ("aditi-rao", 1, 0)
    source = await counts(world.db, world.template.id, world.template.owner_user_id)
    target = await counts(world.db, copy.id, visitor.user_id)
    assert target == source
    copy_scope = WorkspaceScope(workspace_id=copy.id, user_id=visitor.user_id)
    mem = Memory(sql_memory(world.db))
    new_ids = {i.id for i in await mem.reader(copy_scope).find_items(limit=1000)}
    assert new_ids.isdisjoint(set(world.seeded.items.values()))
    # The copy's own "me" carries on from the template's.
    me = await mem.reader(copy_scope).self_entity()
    assert me.workspace_id == copy.id


async def test_a_copy_takes_under_two_seconds_at_the_95th_percentile(
    world: PersonaWorld, identity: SqlIdentityStore
) -> None:
    times: list[float] = []
    for _ in range(12):
        visitor = await workspace(identity)
        started = time.perf_counter()
        await world.service().make_copy(visitor.user_id)
        times.append(time.perf_counter() - started)
    times.sort()
    assert times[round(0.95 * (len(times) - 1))] < 2.0, times


async def test_every_table_with_a_workspace_id_is_copied_or_skipped_on_purpose(
    owner_db: Database,
) -> None:
    async with owner_db.engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT DISTINCT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'workspace_id'"
            )
        )
        tables = {r[0] for r in rows}
    copied, skipped = workspace_tables()
    assert set(TABLES) == copied
    assert tables == copied | set(skipped), (
        "a workspace-owned table the copy doesn't know: "
        f"{sorted(tables - copied - set(skipped))}; add it to COPIED or SKIPPED (S4.11)"
    )


async def test_the_copy_function_refuses_anything_but_a_template(
    world: PersonaWorld, app_db: Database, identity: SqlIdentityStore
) -> None:
    victim = await workspace(identity)  # an ordinary person's workspace
    thief = await workspace(identity)
    thief_copy = await identity.create_workspace(
        owner_user_id=thief.user_id, kind=WorkspaceKind.PERSONA_COPY, timezone="UTC"
    )
    with pytest.raises(DBAPIError, match="not a persona template"):
        await world.service().store.copy_template(
            template_id=victim.workspace_id,
            workspace_id=thief_copy.id,
            user_id=thief.user_id,
            moved_days=0,
        )
    # Nor into a workspace that has something in it, nor into someone else's.
    other = await workspace(identity)
    mine = await world.service().make_copy(other.user_id)
    with pytest.raises(DBAPIError, match=r"not empty|not a new persona copy"):
        await world.service().store.copy_template(
            template_id=world.template.id, workspace_id=mine.id, user_id=other.user_id, moved_days=0
        )
    with pytest.raises(DBAPIError, match="not a new persona copy"):
        await world.service().store.copy_template(
            template_id=world.template.id,
            workspace_id=thief_copy.id,
            user_id=victim.user_id,  # not the owner
            moved_days=0,
        )


async def test_a_copy_is_moved_by_whole_days_and_routines_keep_their_weekday(
    world: PersonaWorld, identity: SqlIdentityStore
) -> None:
    visitor = await workspace(identity)
    copy = await world.service().make_copy(visitor.user_id, moved_days=60)
    assert copy.moved_days == 60
    mem = Memory(sql_memory(world.db))
    t_scope = WorkspaceScope(workspace_id=world.template.id, user_id=world.template.owner_user_id)
    c_scope = WorkspaceScope(workspace_id=copy.id, user_id=visitor.user_id)
    before = {i.title: i for i in await mem.reader(t_scope).find_items(limit=1000)}
    after = {i.title: i for i in await mem.reader(c_scope).find_items(limit=1000)}
    assert before.keys() == after.keys()

    dentist_b, dentist_a = before["Book the dentist"], after["Book the dentist"]
    assert dentist_b.due_at is not None
    assert dentist_a.due_at == dentist_b.due_at + timedelta(days=60)
    assert dentist_a.mentioned_at == dentist_b.mentioned_at + timedelta(days=60)

    run_b, run_a = before["Morning runs with Anika"], after["Morning runs with Anika"]
    assert run_b.occurred_start is not None
    moved = run_a.occurred_start - run_b.occurred_start  # type: ignore[operator]
    assert moved == timedelta(days=56)  # whole weeks: the weekday is kept
    assert moved.days % 7 == 0

    # The keys that name a date say the new one, and keep the template's vector.
    t_keys = {k.key_kind: k for k in await mem.reader(t_scope).keys([dentist_b.id])}
    c_keys = {k.key_kind: k for k in await mem.reader(c_scope).keys([dentist_a.id])}
    verbal_b, verbal_a = t_keys[KeyKind.VERBAL], c_keys[KeyKind.VERBAL]
    assert verbal_a.text != verbal_b.text
    assert verbal_a.embedding == verbal_b.embedding
    assert verbal_a.embedding_model == verbal_b.embedding_model


async def test_changes_in_a_copy_never_reach_the_template_or_another_copy(
    world: PersonaWorld, identity: SqlIdentityStore
) -> None:
    a = await workspace(identity)
    b = await workspace(identity)
    copy_a = await world.service().make_copy(a.user_id, moved_days=0)
    copy_b = await world.service().make_copy(b.user_id, moved_days=0)
    mem = Memory(sql_memory(world.db))
    scope_a = WorkspaceScope(workspace_id=copy_a.id, user_id=a.user_id)
    async with world.db.workspace(scope_a) as session:
        await session.execute(text("UPDATE memory_items SET title = 'Changed by A'"))
        await session.execute(text("DELETE FROM triggers"))
    for owner, ws_id in ((world.template.owner_user_id, world.template.id), (b.user_id, copy_b.id)):
        scope = WorkspaceScope(workspace_id=ws_id, user_id=owner)
        items = await mem.reader(scope).find_items(limit=1000)
        assert not any(i.title == "Changed by A" for i in items)
    assert (await counts(world.db, copy_b.id, b.user_id))["triggers"] > 0
    # Neither copy can see the other's rows, and a workspace can't be entered by another user.
    with pytest.raises(NotFoundError):
        await resolve_scope(identity, user_id=b.user_id, workspace_id=copy_a.id)
    async with world.db.workspace(WorkspaceScope(workspace_id=copy_b.id, user_id=b.user_id)) as s:
        seen = {
            r[0] for r in await s.execute(text("SELECT DISTINCT workspace_id FROM memory_items"))
        }
    assert seen == {copy_b.id}


async def test_one_copy_per_person_and_reset_replaces_it(
    world: PersonaWorld, identity: SqlIdentityStore
) -> None:
    visitor = await workspace(identity)
    svc = world.service()
    first = await svc.copy_for(visitor.user_id)
    again = await svc.copy_for(visitor.user_id)
    assert again.id == first.id
    fresh = await svc.reset_for(visitor.user_id)
    assert fresh.id != first.id
    assert await identity.get_workspace(first.id) is None
    assert [
        w.id
        for w in await identity.workspaces_for(visitor.user_id)
        if w.kind is WorkspaceKind.PERSONA_COPY
    ] == [fresh.id]


async def test_the_copy_is_moved_to_today_in_the_personas_calendar(world: PersonaWorld) -> None:
    anchor = world.seed.anchor
    # 23:30 in Bengaluru on the sixtieth day after the anchor: still that day, not the next.
    later = datetime(2026, 12, 4, 18, 0, tzinfo=UTC)
    assert world.service(today=later).moved_days == 60
    assert world.service(today=later + timedelta(hours=1)).moved_days == 61  # past midnight there
    assert world.service(today=anchor - timedelta(days=3)).moved_days == 0  # never backwards

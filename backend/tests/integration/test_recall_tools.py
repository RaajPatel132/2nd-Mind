"""S3.5 / S3.6: the seven retrieval tools and the soft channel, as real SQL on the recall
fixture: what each finds, the mandatory filters in every one, read-only transactions, and
nothing from another workspace."""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from secondmind.auth.adapters import SqlIdentityStore
from secondmind.core import EntityRole, KeyKind, Kind, TimeClock, new_id
from secondmind.memory.adapters import Database
from secondmind.retrieval import Access, Filters, PathHop, Query, SetOp, WindowFilter
from secondmind.retrieval.adapters import SqlRecallStore
from tests.integration.recall_seed import EMBED_MODEL, RecallWorkspaces, embed_one, seed_recall

pytestmark = pytest.mark.integration

TZ = ZoneInfo("Asia/Kolkata")
OPEN = Access()
SENSITIVE = Access(sensitive=True)


def local(text_: str) -> datetime:
    return datetime.fromisoformat(text_).replace(tzinfo=TZ)


def window(start: str | None, end: str | None, clock: TimeClock = TimeClock.OCCURRED):
    return WindowFilter(
        clock=clock,
        start=local(start) if start else None,
        end=local(end) if end else None,
    )


SEPTEMBER = window("2026-09-01T00:00", "2026-10-01T00:00")
RUNS = Filters(
    kinds=(Kind.EPISODE,), subtypes=("measurement",), attribute=("activity", "run"),
    window=SEPTEMBER,
)  # fmt: skip


@pytest.fixture(scope="module")
async def recall(app_db: Database, identity: SqlIdentityStore) -> RecallWorkspaces:
    return await seed_recall(app_db, identity)


def store(db: Database, recall: RecallWorkspaces, *, other: bool = False) -> SqlRecallStore:
    return SqlRecallStore(db, (recall.other if other else recall.main).scope, timeout_ms=5_000)


def keys(recall: RecallWorkspaces, ids: object) -> list[str | None]:
    return [recall.main.key_of(i) for i in ids]  # type: ignore[attr-defined]


async def query(text_: str) -> Query:
    return Query(text=text_, vector=await embed_one(text_), model=EMBED_MODEL)


# ------------------------------------------------------------------ lookup


async def test_lookup_filters_on_kind_subtype_and_state(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    found = await store(app_db, recall).lookup(
        Filters(kinds=(Kind.INTENTION,), subtypes=("watch",), states=("wanted",)), OPEN
    )
    assert set(keys(recall, [h.item_id for h in found.hits])) == {"watch_dark", "watch_mindhunter"}
    assert found.total == 2


async def test_lookup_latest_value_is_the_current_row(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    me = recall.main.entities["self"]
    s = store(app_db, recall)
    current = await s.lookup(
        Filters(entity_ids=(me,), predicate="lives_in", current_only=True), OPEN
    )
    assert keys(recall, [h.item_id for h in current.hits]) == ["live_pune"]
    both = await s.lookup(Filters(entity_ids=(me,), predicate="lives_in"), OPEN)
    assert keys(recall, [h.item_id for h in both.hits]) == ["live_pune", "live_bengaluru"]


async def test_lookup_category_includes_sub_categories(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    found = await store(app_db, recall).lookup(Filters(category="food"), OPEN)
    got = set(keys(recall, [h.item_id for h in found.hits]))
    assert {"vegetarian", "try_blue_lotus", "brunch_nisha", "dinner_tonight"} <= got


async def test_lookup_set_places_to_try_with_no_episode_at_them(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    found = await store(app_db, recall).lookup(
        Filters(kinds=(Kind.INTENTION,), subtypes=("try",), states=("wanted",)),
        OPEN,
        set_op=SetOp(op="without", other_kind=Kind.EPISODE),
    )
    assert set(keys(recall, [h.item_id for h in found.hits])) == {"try_blue_lotus", "try_saffron"}


async def test_lookup_set_shows_kabir_and_i_both_want_to_watch(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    found = await store(app_db, recall).lookup(
        Filters(kinds=(Kind.INTENTION,), subtypes=("watch",), states=("wanted",)),
        OPEN,
        set_op=SetOp(op="shared_with", entity_id=recall.main.entities["kabir"]),
    )
    assert keys(recall, [h.item_id for h in found.hits]) == ["watch_dark"]


async def test_lookup_by_entity_and_role(app_db: Database, recall: RecallWorkspaces) -> None:
    kabir = recall.main.entities["kabir"]
    found = await store(app_db, recall).lookup(
        Filters(entity_ids=(kabir,), roles=(EntityRole.WITH,)), OPEN
    )
    got = set(keys(recall, [h.item_id for h in found.hits]))
    assert {"goa_trip", "watch_dark", "dinner_tonight", "run_misfiled_subtype"} <= got
    assert "kabir_likes_mk" not in got  # about Kabir, not with him


# ------------------------------------------------------------------ aggregate


async def test_aggregate_counts_exactly_and_returns_the_ids_it_counted(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    result = await store(app_db, recall).aggregate(RUNS, OPEN, op="count")
    assert result.value == 6
    assert set(keys(recall, result.item_ids)) == {
        "run_sep_02", "run_sep_06", "run_sep_09", "run_sep_13", "run_sep_20", "run_sep_27",
    }  # fmt: skip


async def test_aggregate_sums_a_value_and_groups_by_week(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    result = await store(app_db, recall).aggregate(
        RUNS, OPEN, op="sum", field="value", group_by="week", timezone="Asia/Kolkata"
    )
    assert result.value == 5 + 8 + 5 + 10 + 12 + 6
    weeks = {g.key: g.value for g in result.groups}
    assert weeks["2026-W36"] == 5 + 8  # Wed 2 and Sun 6 September
    assert sum(weeks.values()) == 46


# ------------------------------------------------------------------ search and the soft channel


async def test_filtered_search_misses_a_filed_wrong_item_the_soft_channel_finds(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    s = store(app_db, recall)
    q = await query("What did I read about sleep?")
    filtered = await s.search(q, Filters(kinds=(Kind.RESOURCE,)), OPEN, limit=20)
    assert "sleep_article" not in keys(recall, [h.item_id for h in filtered])
    soft = await s.search(q, Filters(), OPEN, limit=20)
    ranked = keys(recall, [h.item_id for h in soft])
    assert ranked[0] == "sleep_article"
    top = soft[0]
    assert top.lexical is not None
    assert top.dense is not None
    assert top.matched_key in (KeyKind.TEXT, KeyKind.VERBAL)


async def test_one_memory_with_several_matching_keys_takes_one_slot(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    soft = await store(app_db, recall).search(await query("Pune"), Filters(), OPEN, limit=50)
    ids = [h.item_id for h in soft]
    assert len(ids) == len(set(ids))
    assert recall.main.items["live_pune"] in ids


async def test_search_finds_a_key_by_its_cue(app_db: Database, recall: RecallWorkspaces) -> None:
    soft = await store(app_db, recall).search(
        await query("planning the Japan trip"), Filters(), OPEN, limit=5
    )
    assert keys(recall, [h.item_id for h in soft])[0] == "ramen_japan"


# ------------------------------------------------------------------ entity (two hops)


async def test_entity_follows_a_relation_path(app_db: Database, recall: RecallWorkspaces) -> None:
    nisha = recall.main.entities["nisha"]
    result = await store(app_db, recall).entity([nisha], OPEN, path=[PathHop("spouse_of")])
    assert result.entity_ids == [recall.main.entities["rohan"]]
    assert result.paths == [["Nisha", "spouse_of", "Rohan"]]
    got = keys(recall, [h.item_id for h in result.hits])
    assert {"rohan_jazz", "rohan_birds", "rohan_husband"} <= set(got)


async def test_entity_follows_two_hops_my_sisters_husband(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    me = recall.main.entities["self"]
    result = await store(app_db, recall).entity(
        [me], OPEN, path=[PathHop("sibling_of"), PathHop("spouse_of")]
    )
    assert result.entity_ids == [recall.main.entities["rohan"]]
    assert result.paths[0][-1] == "Rohan"


async def test_entity_with_no_such_relation_reaches_nobody(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    kabir = recall.main.entities["kabir"]
    result = await store(app_db, recall).entity([kabir], OPEN, path=[PathHop("spouse_of")])
    assert result.entity_ids == []
    assert result.hits == []


# ------------------------------------------------------------------ timeline


async def test_timeline_expands_routine_occurrences_inside_the_window(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    result = await store(app_db, recall).timeline(
        window("2026-10-12T00:00", "2026-10-19T00:00"),
        Filters(),
        OPEN,
        now=recall.fixture.instant,
        timezone="Asia/Kolkata",
    )
    gym = recall.main.items["gym_routine"]
    at = [
        o.start.astimezone(TZ).strftime("%a %d %H:%M")
        for o in result.occurrences
        if o.item_id == gym
    ]
    assert at == ["Mon 12 07:00", "Wed 14 07:00", "Fri 16 07:00"]
    assert all(o.upcoming for o in result.occurrences)


async def test_timeline_this_week_marks_past_and_upcoming(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    result = await store(app_db, recall).timeline(
        window("2026-10-05T00:00", "2026-10-12T00:00"),
        Filters(),
        OPEN,
        now=recall.fixture.instant,
        timezone="Asia/Kolkata",
    )
    seen = {(recall.main.key_of(o.item_id), o.via, o.upcoming) for o in result.occurrences}
    assert ("gym_routine", "routine", False) in seen  # Monday 5 October, already past
    assert ("gym_routine", "routine", True) in seen
    assert ("dinner_tonight", "occurred", True) in seen
    assert ("dinner_tonight", "trigger", True) in seen  # its reminder at 18:00
    assert ("saturday_workshop", "occurred", True) in seen


async def test_timeline_includes_tasks_due_and_goals_with_a_target_in_the_window(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    s = store(app_db, recall)
    now = recall.fixture.instant
    october = await s.timeline(
        window("2026-10-06T10:00", "2026-11-05T10:00"), Filters(), OPEN, now=now,
        timezone="Asia/Kolkata",
    )  # fmt: skip
    got = {(recall.main.key_of(o.item_id), o.via) for o in october.occurrences}
    assert ("renew_passport", "due") in got
    assert ("renew_passport", "trigger") in got
    february = await s.timeline(
        window("2027-02-01T00:00", "2027-03-01T00:00"), Filters(), OPEN, now=now,
        timezone="Asia/Kolkata",
    )  # fmt: skip
    assert ("half_marathon_goal", "due") in {
        (recall.main.key_of(o.item_id), o.via) for o in february.occurrences
    }


# ------------------------------------------------------------------ history


async def test_history_is_oldest_to_newest_with_validity_and_the_change_key(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    result = await store(app_db, recall).history(
        OPEN, subject_entity_id=recall.main.entities["self"], predicate="lives_in"
    )
    assert keys(recall, [r.item_id for r in result.rows]) == ["live_bengaluru", "live_pune"]
    old, new = result.rows
    assert old.valid_to is not None
    assert old.state == "superseded"
    assert new.valid_to is None
    assert new.change_key is not None
    assert "Bengaluru" in new.change_key


async def test_history_of_an_item_walks_the_chain_and_its_because_links(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    s = store(app_db, recall)
    moved = await s.history(OPEN, item_ids=[recall.main.items["dentist_new"]])
    assert keys(recall, [r.item_id for r in moved.rows]) == ["dentist_old", "dentist_new"]
    why = await s.history(OPEN, item_ids=[recall.main.items["gym_cancel"]])
    assert [(recall.main.key_of(a), recall.main.key_of(b)) for a, b in why.because] == [
        ("gym_cancel", "gym_reason")
    ]


# ------------------------------------------------------------------ conversation


async def test_conversation_finds_what_i_suggested_in_that_window(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    # Offline embeddings are a bag of words, so the query shares words with one list item (with
    # "the books you suggested" nothing scores and the order is a tie between random ids).
    hits = await store(app_db, recall).conversation(
        await query("the gentle short stories you suggested"),
        role="assistant",
        window=window("2026-09-28T00:00", "2026-10-05T00:00", TimeClock.MENTIONED),
        exclude_turn=None,
    )
    assert hits
    assert hits[0].turn_id == recall.main.turns["books"]
    assert hits[0].role == "assistant"
    # The best snippet of that reply: one of the three list items (a snippet per list item).
    # The best snippet of that reply is its list item, not the whole reply.
    assert "Tea by the Window" in hits[0].text
    assert "Map of Small" not in hits[0].text


async def test_conversation_never_returns_the_turn_asking(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    books = recall.main.turns["books"]
    hits = await store(app_db, recall).conversation(
        await query("suggest three books for a slow weekend"),
        role=None,
        window=None,
        exclude_turn=books,
    )
    assert books not in {h.turn_id for h in hits}


# ------------------------------------------------------------------ mandatory filters


async def test_sensitive_is_returned_only_when_the_question_is_about_it(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    s = store(app_db, recall)
    therapist = recall.main.items["therapist"]
    q = await query("When do I see my therapist?")
    thursday = Filters(kinds=(Kind.FACT,))
    for access, expected in ((OPEN, False), (SENSITIVE, True)):
        looked = await s.lookup(thursday, access)
        assert (therapist in {h.item_id for h in looked.hits}) is expected
        searched = await s.search(q, Filters(), access, limit=50)
        assert (therapist in {h.item_id for h in searched}) is expected
        entity = await s.entity([recall.main.entities["self"]], access, limit=200)
        assert (therapist in {h.item_id for h in entity.hits}) is expected
        history = await s.history(
            access, subject_entity_id=recall.main.entities["self"], predicate="therapy"
        )
        assert (therapist in {r.item_id for r in history.rows}) is expected
        counted = await s.aggregate(thursday, access, op="count")
        assert (therapist in counted.item_ids) is expected
        timeline = await s.timeline(
            window("2026-08-01T00:00", "2026-09-01T00:00", TimeClock.MENTIONED), Filters(),
            access, now=recall.fixture.instant, timezone="Asia/Kolkata",
        )  # fmt: skip
        assert (therapist in {o.item_id for o in timeline.occurrences}) is expected


@pytest.fixture
async def secret_and_deleted(
    owner_db: Database, recall: RecallWorkspaces
) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    """A secret row and a deleted row, written behind the writer's back (it never would)."""
    ids = (new_id(), new_id())
    ws = recall.main.scope.workspace_id
    turn = recall.main.system_turn
    async with owner_db.identity() as s:
        for item_id, sensitivity, status in (
            (ids[0], "secret", "active"),
            (ids[1], "normal", "deleted"),
        ):
            await s.execute(
                text(
                    "INSERT INTO memory_items (id, workspace_id, created_by_turn_id, "
                    "updated_by_turn_id, source, trust, status, kind, state, text, title, "
                    "mentioned_at, sensitivity) VALUES (:id, :ws, :t, :t, 'user_message', "
                    "'user_stated', :status, 'fact', 'current', 'My locker code is zebra', "
                    "'locker code zebra', now(), :sens)"
                ),
                {"id": item_id, "ws": ws, "t": turn, "status": status, "sens": sensitivity},
            )
            await s.execute(
                text(
                    "INSERT INTO memory_keys (id, workspace_id, item_id, key_kind, text, "
                    "content_hash) VALUES (:k, :ws, :id, 'text', 'My locker code is zebra', 'x')"
                ),
                {"k": new_id(), "ws": ws, "id": item_id},
            )
    yield ids
    async with owner_db.identity() as s:
        await s.execute(text("DELETE FROM memory_items WHERE id = ANY(:ids)"), {"ids": list(ids)})


async def test_secret_and_deleted_rows_are_never_returned(
    app_db: Database, recall: RecallWorkspaces, secret_and_deleted: tuple[uuid.UUID, uuid.UUID]
) -> None:
    s = store(app_db, recall)
    hidden = set(secret_and_deleted)
    q = Query(text="locker code zebra", vector=None, model=EMBED_MODEL)
    assert not hidden & {h.item_id for h in await s.search(q, Filters(), SENSITIVE, limit=50)}
    assert not hidden & {h.item_id for h in (await s.lookup(Filters(), SENSITIVE, limit=500)).hits}
    counted = await s.aggregate(Filters(kinds=(Kind.FACT,)), SENSITIVE, op="count")
    assert not hidden & set(counted.item_ids)
    entity = await s.entity([recall.main.entities["self"]], SENSITIVE, limit=500)
    assert not hidden & {h.item_id for h in entity.hits}
    history = await s.history(SENSITIVE, item_ids=list(hidden))
    assert history.rows == []
    timeline = await s.timeline(
        window(None, None, TimeClock.MENTIONED), Filters(), SENSITIVE,
        now=recall.fixture.instant, timezone="Asia/Kolkata",
    )  # fmt: skip
    assert not hidden & {o.item_id for o in timeline.occurrences}


async def test_tools_run_in_a_read_only_transaction(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    with pytest.raises(DBAPIError, match="read-only transaction"):
        async with app_db.workspace(recall.main.scope, read_only=True) as session:
            await session.execute(text("UPDATE memory_items SET title = 'tampered'"))


# ------------------------------------------------------------------ isolation


async def test_every_tool_under_another_workspace_finds_nothing_from_this_one(
    app_db: Database, recall: RecallWorkspaces
) -> None:
    other = store(app_db, recall, other=True)
    mine = set(recall.main.items.values())
    now = recall.fixture.instant
    q = await query("What does Nisha's husband like? Nisha Rohan runs Pune books")
    looked = await other.lookup(Filters(), SENSITIVE, limit=500)
    assert not mine & {h.item_id for h in looked.hits}
    assert not mine & set((await other.aggregate(Filters(), SENSITIVE, op="count")).item_ids)
    soft = await other.search(q, Filters(), SENSITIVE, limit=50)
    assert soft  # the other workspace's own Nisha and Rohan
    assert not mine & {h.item_id for h in soft}
    entity = await other.entity(
        [recall.main.entities["nisha"]], SENSITIVE, path=[PathHop("spouse_of")]
    )
    assert entity.entity_ids == []
    assert entity.hits == []
    timeline = await other.timeline(
        window(None, None), Filters(), SENSITIVE, now=now, timezone="Asia/Kolkata"
    )
    assert not mine & {o.item_id for o in timeline.occurrences}
    history = await other.history(SENSITIVE, item_ids=list(mine))
    assert history.rows == []
    said = await other.conversation(q, role=None, window=None, exclude_turn=None)
    assert {h.turn_id for h in said} <= set(recall.other.turns.values())

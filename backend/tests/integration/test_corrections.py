"""S3.12 on Postgres and the recall fixture: reclassify, correct a wrong value, forget, a bulk
re-file, a "yes" to the count check's offer, and an edit in place. Each is a turn with a diff
that undo reverses."""

import uuid
from typing import Any

import pytest

from secondmind.agent import Turn, TurnKind, TurnRunner
from secondmind.agent.adapters import SqlTurnStore
from secondmind.auth.adapters import SqlIdentityStore
from secondmind.core import (
    IntentEvent,
    ItemStatus,
    Kind,
    LinkType,
    MemoryDiffEvent,
    RetrievalEvent,
    WorkspaceScope,
)
from secondmind.corrections import CorrectionChanges
from secondmind.evals.fixture import Seeded, load_fixture
from secondmind.evals.recall import case_replay, eval_runner, load_cases, replay_router, seed_main
from secondmind.memory.adapters import Database
from secondmind.retrieval import Access
from secondmind.retrieval.adapters import SqlRecallStore

pytestmark = pytest.mark.integration

INTENT = {"intent": "correct", "confidence": 0.9, "reason": "It corrects a saved memory."}
MINDHUNTER = "No, Mindhunter is a series, not a film."
COUSIN = "No, Rohan is Nisha's cousin, not her husband."
FORGET = "Forget the sleep article."
BULK = "File every run I logged under sport/running."
REPLAY: dict[str, dict[str, Any]] = {
    MINDHUNTER: {
        "intent": INTENT,
        "correct": {
            "type": "reclassify",
            "target": {"source": "search", "ref": "c1"},
            "changes": {"tags": ["series"], "category": "entertainment/series"},
            "reason": "It was filed without saying it's a series.",
        },
    },
    COUSIN: {
        "intent": INTENT,
        "correct": {
            "type": "wrong_value",
            "target": {"source": "search", "ref": "c1"},
            "new_text": "Rohan is Nisha's cousin.",
            "relation": {
                "subject": "Rohan",
                "object": "Nisha",
                "wrong": "spouse_of",
                "right": "cousin_of",
            },
            "reason": "Rohan was never Nisha's husband.",
        },
    },
    FORGET: {
        "intent": INTENT,
        "correct": {
            "type": "forget",
            "target": {"source": "search", "ref": "c1"},
            "reason": "They want the sleep article gone.",
        },
    },
    BULK: {
        "intent": INTENT,
        "correct": {
            "type": "bulk",
            "target": {"source": "none"},
            "match": "ran",
            "category": "sport/running",
            "reason": "Re-file the runs.",
        },
    },
}


@pytest.fixture(scope="module")
def router():  # type: ignore[no-untyped-def]
    return replay_router({**case_replay(load_cases()), **REPLAY})


DB: list[Database] = []


async def say(runner: TurnRunner, scope: WorkspaceScope, text: str) -> Turn:
    handle = await runner.start(scope, text=text, timezone=load_fixture().timezone)
    async for _ in handle.events():
        pass
    turn = await SqlTurnStore(DB[0], scope).get(handle.turn.id)
    assert turn is not None
    return turn


async def events(runner: TurnRunner, scope: WorkspaceScope, turn: Turn) -> list[Any]:
    return [e.event for e in await SqlTurnStore(DB[0], scope).events(turn.id)]


async def world(db: Database, identity: SqlIdentityStore, router) -> tuple[Seeded, TurnRunner]:  # type: ignore[no-untyped-def]
    DB[:] = [db]
    seeded = await seed_main(db, identity, router)
    return seeded, eval_runner(db, router, now=load_fixture().instant)


async def test_a_misfiled_memory_is_reclassified_with_an_update(
    app_db: Database, identity: SqlIdentityStore, router: Any
) -> None:
    seeded, runner = await world(app_db, identity, router)
    turn = await say(runner, seeded.scope, MINDHUNTER)
    item = await runner.memory.reader(seeded.scope).item(seeded.items["watch_mindhunter"])
    assert item is not None
    assert item.tags == ["series"], turn.output
    diff = [e for e in await events(runner, seeded.scope, turn) if isinstance(e, MemoryDiffEvent)]
    assert [entry.op for d in diff for entry in d.entries] == ["updated"]

    await runner.undo(seeded.scope, turn_id=turn.id, timezone="Asia/Kolkata")
    item = await runner.memory.reader(seeded.scope).item(seeded.items["watch_mindhunter"])
    assert item is not None
    assert item.tags == []


async def test_a_wrong_value_is_corrected_not_superseded(
    app_db: Database, identity: SqlIdentityStore, router: Any
) -> None:
    seeded, runner = await world(app_db, identity, router)
    scope, old_id = seeded.scope, seeded.items["rohan_husband"]
    turn = await say(runner, scope, COUSIN)
    reader = runner.memory.reader(scope)
    old = await reader.item(old_id)
    assert old is not None
    assert old.status is ItemStatus.ARCHIVED, turn.output
    (link,) = [lk for lk in await reader.links([old_id]) if lk.link_type is LinkType.CORRECTS]
    new = await reader.item(link.src_item_id)
    assert new is not None
    assert new.text == "Rohan is Nisha's cousin."

    rohan, nisha = seeded.entities["rohan"], seeded.entities["nisha"]
    live = {r.relation for r in await reader.relations([rohan]) if r.valid_to is None}
    assert "cousin_of" in live
    assert "spouse_of" not in live

    # A mistake isn't history, and its key says it was a mistake.
    history = await SqlRecallStore(app_db, scope).history(Access(), item_ids=[old_id, new.id])
    assert old_id not in {r.item_id for r in history.rows}
    assert any("by mistake" in k.text for k in await reader.keys([old_id]))

    # The 2-hop question now has no one to answer for.
    after = await say(runner, scope, "What does Nisha's husband like?")
    assert (after.output or "").startswith("I don't have anything saved about"), after.output
    assert nisha


async def test_forget_is_held_then_deleted_and_undo_restores_it(
    app_db: Database, identity: SqlIdentityStore, router: Any
) -> None:
    seeded, runner = await world(app_db, identity, router)
    scope, item_id = seeded.scope, seeded.items["sleep_article"]
    turn = await say(runner, scope, FORGET)
    assert "Held for your confirmation" in (turn.output or ""), turn.output
    (held,) = await runner.memory.reader(scope).held_writes(status="pending")
    confirm = await runner.confirm_held(scope, held_id=held.id, timezone="Asia/Kolkata")
    item = await runner.memory.reader(scope).item(item_id)
    assert item is not None
    assert item.status is ItemStatus.DELETED

    await runner.undo(scope, turn_id=confirm.id, timezone="Asia/Kolkata")
    item = await runner.memory.reader(scope).item(item_id)
    assert item is not None
    assert item.status is ItemStatus.ACTIVE


async def test_a_bulk_refile_over_the_threshold_is_held(
    app_db: Database, identity: SqlIdentityStore, router: Any
) -> None:
    seeded, runner = await world(app_db, identity, router)
    turn = await say(runner, seeded.scope, BULK)
    assert "Held for your confirmation" in (turn.output or ""), turn.output
    assert "memories match" in (turn.output or "")
    item = await runner.memory.reader(seeded.scope).item(seeded.items["run_sep_02"])
    categories = {c.id: c.slug for c in await runner.memory.reader(seeded.scope).categories()}
    assert item is not None
    assert categories[item.category_id] == "health/fitness"  # type: ignore[index]


async def test_yes_to_the_count_offer_files_the_look_alikes(
    app_db: Database, identity: SqlIdentityStore, router: Any
) -> None:
    seeded, runner = await world(app_db, identity, router)
    question = "How many runs did I log in September?"
    first = await say(runner, seeded.scope, question)
    assert "File them as runs?" in (first.output or ""), first.output

    yes = await say(runner, seeded.scope, "yes")
    (intent,) = [e for e in await events(runner, seeded.scope, yes) if isinstance(e, IntentEvent)]
    assert (intent.intent.value, intent.source) == ("correct", "rule")
    note = await runner.memory.reader(seeded.scope).item(seeded.items["run_misfiled_note"])
    assert note is not None
    assert note.kind is Kind.EPISODE
    assert note.subtype == "measurement"
    assert note.attributes.get("activity") == "run"

    again = await say(runner, seeded.scope, question)
    (event,) = [
        e for e in await events(runner, seeded.scope, again) if isinstance(e, RetrievalEvent)
    ]
    assert event.sub_queries[0].aggregate is not None
    assert event.sub_queries[0].aggregate.value == 8


async def test_an_edit_in_place_is_its_own_undoable_turn(
    app_db: Database, identity: SqlIdentityStore, router: Any
) -> None:
    seeded, runner = await world(app_db, identity, router)
    item_id: uuid.UUID = seeded.items["saturday_workshop"]
    before = await runner.memory.reader(seeded.scope).item(item_id)
    changes = CorrectionChanges.model_validate(
        dict.fromkeys(CorrectionChanges.model_fields)
        | {"date_expression": "Sunday", "date_clock": "occurred"}
    )
    turn = await runner.edit_item(
        seeded.scope, item_id=item_id, changes=changes, timezone="Asia/Kolkata"
    )
    assert turn.kind is TurnKind.EDIT
    assert turn.status.value == "completed", turn.output
    after = await runner.memory.reader(seeded.scope).item(item_id)
    assert before is not None
    assert after is not None
    assert after.occurred_start is not None
    # Sunday 11 October, local midnight
    assert after.occurred_start.isoformat().startswith("2026-10-10T18:30")
    await runner.undo(seeded.scope, turn_id=turn.id, timezone="Asia/Kolkata")
    undone = await runner.memory.reader(seeded.scope).item(item_id)
    assert undone is not None
    assert undone.occurred_start == before.occurred_start

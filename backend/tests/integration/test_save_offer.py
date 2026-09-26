"""S3.9 on Postgres and the recall fixture: an answer that rests on something I said offers to
save it, and a "yes" saves those words (not the "yes") as its own undoable write."""

from typing import Any

import pytest

from secondmind.agent import Turn, TurnRunner
from secondmind.agent.adapters import SqlTurnStore
from secondmind.auth.adapters import SqlIdentityStore
from secondmind.core import (
    IntentEvent,
    ItemStatus,
    MemoryDiffEvent,
    RetrievalEvent,
    TargetType,
    WorkspaceScope,
)
from secondmind.evals.fixture import load_fixture
from secondmind.evals.recall import eval_runner, fake_router, load_cases, seed_main
from secondmind.memory.adapters import Database
from secondmind.retrieval import SAVE_OFFER

pytestmark = pytest.mark.integration

BOOKS = "What books did you suggest for a slow weekend?"


async def say(db: Database, runner: TurnRunner, scope: WorkspaceScope, text: str) -> Turn:
    handle = await runner.start(scope, text=text, timezone=load_fixture().timezone)
    async for _ in handle.events():
        pass
    turn = await SqlTurnStore(db, scope).get(handle.turn.id)
    assert turn is not None
    return turn


async def events(db: Database, scope: WorkspaceScope, turn: Turn) -> list[Any]:
    return [e.event for e in await SqlTurnStore(db, scope).events(turn.id)]


async def saved_by(runner: TurnRunner, scope: WorkspaceScope, turn: Turn) -> list[str]:
    """The text of the active memories the turn wrote."""
    reader = runner.memory.reader(scope)
    rows = await reader.write_log(turn.id)
    ids = list(dict.fromkeys(r.target_id for r in rows if r.target_type is TargetType.ITEM))
    return [i.text for i in await reader.items(ids) if i.status is ItemStatus.ACTIVE]


async def test_yes_to_the_save_offer_saves_what_i_suggested_and_undo_removes_it(
    app_db: Database, identity: SqlIdentityStore
) -> None:
    router = fake_router(load_cases())
    seeded = await seed_main(app_db, identity, router)
    runner = eval_runner(app_db, router, now=load_fixture().instant)
    scope = seeded.scope

    asked = await say(app_db, runner, scope, BOOKS)
    assert (asked.output or "").endswith(SAVE_OFFER), asked.output
    (retrieval,) = [e for e in await events(app_db, scope, asked) if isinstance(e, RetrievalEvent)]
    offer = retrieval.save_offer
    assert offer is not None
    # The offer holds what the reply cited (the offline answer cites every snippet it was given).
    assert seeded.turns["books"] in offer.turn_ids
    assert any("Tea by the Window" in s for s in offer.said)
    assert await saved_by(runner, scope, asked) == []  # said, not saved

    yes = await say(app_db, runner, scope, "yes")
    assert yes.status.value == "completed", yes.output
    got = await events(app_db, scope, yes)
    (intent,) = [e for e in got if isinstance(e, IntentEvent)]
    assert (intent.intent.value, intent.source) == ("save", "rule")
    assert [e.op for d in got if isinstance(d, MemoryDiffEvent) for e in d.entries] != []
    assert any("Tea by the Window" in t for t in await saved_by(runner, scope, yes))

    await runner.undo(scope, turn_id=yes.id, timezone="Asia/Kolkata")
    assert await saved_by(runner, scope, yes) == []
    await runner.aclose()


async def test_yes_without_an_offer_is_not_a_save_of_what_i_said(
    app_db: Database, identity: SqlIdentityStore
) -> None:
    router = fake_router(load_cases())
    seeded = await seed_main(app_db, identity, router)
    runner = eval_runner(app_db, router, now=load_fixture().instant)
    asked = await say(app_db, runner, seeded.scope, "Where do I live?")
    assert SAVE_OFFER not in (asked.output or "")
    yes = await say(app_db, runner, seeded.scope, "yes")
    (intent,) = [e for e in await events(app_db, seeded.scope, yes) if isinstance(e, IntentEvent)]
    assert intent.source == "model"
    await runner.aclose()

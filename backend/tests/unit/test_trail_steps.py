"""The Trail (UI.5, ADR-0029): which agent steps a turn reports, in what order, with what status,
and that the live stream carries exactly the stored events and steps, in order."""

import uuid

from secondmind.agent import EventRecorded, StepStarted, TurnCompleted, TurnFailed, TurnStreamEvent
from secondmind.core import DiffEntry, MemoryDiffEvent, StepEvent
from secondmind.providers import FakeOutcome, ProviderErrorKind
from tests.unit.test_ingest_turns import Setup, _save

TZ = "Asia/Kolkata"


async def _stored_steps(setup: Setup, turn_id: uuid.UUID) -> list[tuple[str, str]]:
    events = await setup.turns.store(setup.scope).events(turn_id)
    return [(e.event.step, e.event.status) for e in events if isinstance(e.event, StepEvent)]


async def _live_matches_stored(setup: Setup, live: list[TurnStreamEvent]) -> None:
    """Every persisted event was streamed once, in seq order, and each step's start frame came
    before its ``step`` event and in the same order."""
    last = live[-1]
    assert isinstance(last, TurnCompleted | TurnFailed)
    stored = await setup.turns.store(setup.scope).events(last.turn.id)
    streamed = [e.stored for e in live if isinstance(e, EventRecorded)]
    assert [(s.seq, s.event) for s in streamed] == [(s.seq, s.event) for s in stored]
    started = [e.step for e in live if isinstance(e, StepStarted)]
    assert started == [e.event.step for e in stored if isinstance(e.event, StepEvent)]
    for step in started:
        begin = next(i for i, e in enumerate(live) if isinstance(e, StepStarted) and e.step == step)
        end = next(
            i
            for i, e in enumerate(live)
            if isinstance(e, EventRecorded)
            and isinstance(e.stored.event, StepEvent)
            and e.stored.event.step == step
        )
        assert begin < end


async def test_a_save_reports_each_step_that_ran_in_order() -> None:
    setup = Setup(replay=True)
    live = await setup.turn("I live in Bengaluru")
    last = live[-1]
    assert isinstance(last, TurnCompleted)
    assert await _stored_steps(setup, last.turn.id) == [
        ("understand", "done"),
        ("extract", "done"),
        ("entities", "done"),
        ("reconcile", "done"),
        ("guard", "done"),
        ("save", "done"),
        ("enrich", "done"),
        ("answer", "done"),
    ]
    await _live_matches_stored(setup, live)


async def test_a_dated_save_reports_the_dates_step() -> None:
    setup = Setup(replay=True)
    live = await setup.turn(
        "My wife liked this bag from MK, serial no. 123ABC. We can gift it on her birthday "
        "next May."
    )
    last = live[-1]
    assert isinstance(last, TurnCompleted)
    steps = [s for s, _ in await _stored_steps(setup, last.turn.id)]
    assert steps.index("dates") == steps.index("entities") + 1
    await _live_matches_stored(setup, live)


async def test_a_superseding_save_writes_its_diff_with_the_save_step() -> None:
    setup = Setup(replay=True)
    await setup.turn("I live in Bengaluru")
    live = await setup.turn("I moved to Pune")
    last = live[-1]
    assert isinstance(last, TurnCompleted)
    events = [e.event for e in await setup.turns.store(setup.scope).events(last.turn.id)]
    save = next(i for i, e in enumerate(events) if isinstance(e, StepEvent) and e.step == "save")
    guard = next(i for i, e in enumerate(events) if isinstance(e, StepEvent) and e.step == "guard")
    diff = next(i for i, e in enumerate(events) if isinstance(e, MemoryDiffEvent))
    assert guard < diff < save  # the guard's verdicts, then the diff written with its save step
    await _live_matches_stored(setup, live)


async def test_chit_chat_is_two_steps() -> None:
    setup = Setup(replay=True)
    live = await setup.turn("hello!")
    last = live[-1]
    assert isinstance(last, TurnCompleted)
    assert await _stored_steps(setup, last.turn.id) == [("understand", "done"), ("answer", "done")]
    await _live_matches_stored(setup, live)


async def test_a_held_write_holds_the_guard_step() -> None:
    setup = Setup(replay=True)
    live = await setup.turn("I've been seeing a therapist for anxiety since March")
    last = live[-1]
    assert isinstance(last, TurnCompleted)
    steps = dict(await _stored_steps(setup, last.turn.id))
    assert steps["guard"] == "held"
    await _live_matches_stored(setup, live)


async def test_a_refused_secret_refuses_the_guard_step_and_saves_nothing() -> None:
    setup = Setup()
    live = await setup.turn("Remember my wifi password is hunter2")
    last = live[-1]
    assert isinstance(last, TurnCompleted)
    # The pre-check caught it: no model ran, so there is no extract step, and nothing saved.
    assert await _stored_steps(setup, last.turn.id) == [
        ("understand", "done"),
        ("guard", "refused"),
        ("answer", "done"),
    ]
    await _live_matches_stored(setup, live)


async def test_a_provider_failure_mid_extract_fails_that_step_and_nothing_runs_after() -> None:
    setup = Setup(intent=_save)
    setup.fake.script.add(FakeOutcome(error=ProviderErrorKind.SERVER), step="extract")
    live = await setup.turn("Buy more coffee beans")
    last = live[-1]
    assert isinstance(last, TurnFailed)
    assert await _stored_steps(setup, last.turn.id) == [
        ("understand", "done"),
        ("extract", "failed"),
    ]
    stored = await setup.turns.store(setup.scope).events(last.turn.id)
    assert stored[-1].event.type == "error"
    await _live_matches_stored(setup, live)


async def test_undo_and_confirm_turns_report_their_own_step() -> None:
    setup = Setup(replay=True)
    held = (await setup.turn("I've been seeing a therapist for anxiety since March"))[-1]
    assert isinstance(held, TurnCompleted)
    events = [e.event for e in await setup.turns.store(setup.scope).events(held.turn.id)]
    diff = next(e for e in events if isinstance(e, MemoryDiffEvent))
    entry: DiffEntry = next(e for e in diff.entries if e.op == "held")
    assert entry.held_write_id is not None

    confirm = await setup.runner.confirm_held(setup.scope, held_id=entry.held_write_id, timezone=TZ)
    assert await _stored_steps(setup, confirm.id) == [("confirm", "done")]

    undo = await setup.runner.undo(setup.scope, turn_id=confirm.id, timezone=TZ)
    assert await _stored_steps(setup, undo.id) == [("undo", "done")]
    stored = await setup.turns.store(setup.scope).events(undo.id)
    assert isinstance(stored[-1].event, StepEvent)  # the diff is written with its step
    assert any(isinstance(e.event, MemoryDiffEvent) for e in stored)

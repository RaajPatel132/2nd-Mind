"""Save turns through the runner on the fake provider: invalid extraction is retried once and then
fails cleanly (S2.6), a secret never reaches a model, the store or the logs (S2.3), and the core
prefix is served from the provider cache on the next turn (S2.9)."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from secondmind.agent import TurnCompleted, TurnFailed, TurnRunner, TurnStreamEvent
from secondmind.config import DEFAULT_RESOURCES_DIR, PromptRegistry
from secondmind.core import ErrorEvent, KeyKind, ModelCallEvent, WorkspaceScope
from secondmind.ingestion import IngestSettings, load_replay, offline_responders
from secondmind.ingestion.offline import heuristic_note
from secondmind.memory import Memory, MemorySettings
from secondmind.memory.adapters import InMemoryMemory
from secondmind.observability import NullTracer, configure_logging
from secondmind.providers import AdapterRequest, FakeProvider, FakeScript
from tests.fakes import InMemoryTurns
from tests.unit.providers.helpers import router as make_router

PROMPTS = PromptRegistry.load(DEFAULT_RESOURCES_DIR / "prompts")
SECRET = "hunter2"


REPLAY = load_replay(DEFAULT_RESOURCES_DIR / "evals" / "cases" / "ingest")
NOW = datetime(2026, 9, 23, 4, 30, tzinfo=UTC)  # Wednesday, 10:00 in Asia/Kolkata


class RecordingTracer(NullTracer):
    """Keeps every input and output the trace backend would have received."""

    def __init__(self) -> None:
        self.sent: list[object] = []

    def start_turn(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs["turn_input"])
        tracer = self

        class Trace:
            def start_generation(self, step: str) -> Any:
                class Generation:
                    def finish(self, call: Any, **kw: Any) -> None:
                        tracer.sent.extend([kw.get("prompt_input"), kw.get("output")])

                    def fail(self, error: str) -> None:
                        return None

                return Generation()

            def finish(self, **kw: Any) -> None:
                if kw.get("redacted_input") is not None:
                    # The backend overwrites the trace's input with the redacted one.
                    tracer.sent = [x for x in tracer.sent if x != kwargs["turn_input"]]
                    tracer.sent.append(kw["redacted_input"])
                tracer.sent.append(kw.get("output"))

        return Trace()


class Setup:
    def __init__(
        self,
        *,
        replay: bool = False,
        ingest: IngestSettings | None = None,
        memory: MemorySettings | None = None,
        **responders: Any,
    ) -> None:
        script = FakeScript(responders=offline_responders(REPLAY if replay else None))
        self.fake = FakeProvider("primary", script=script)
        self.fake.script.responders.update(responders)
        self.turns = InMemoryTurns(clock=lambda: NOW)
        self.db = InMemoryMemory()
        self.tracer = RecordingTracer()
        self.memory = Memory(self.db.store, memory)
        self.scope = WorkspaceScope(workspace_id=uuid.uuid4(), user_id=uuid.uuid4())
        self.runner = TurnRunner(
            router=make_router({"primary": self.fake}, "primary:m", None, all_steps=True),
            prompts=PROMPTS,
            stores=self.turns.store,
            tracer=self.tracer,
            config_hash="c" * 64,
            max_message_chars=500,
            memory=self.memory,
            ingest=ingest,
            clock=lambda: NOW,
        )

    async def steps(self, turn_id: uuid.UUID) -> list[str]:
        events = await self.turns.store(self.scope).events(turn_id)
        return [e.event.step for e in events if isinstance(e.event, ModelCallEvent)]

    async def turn(self, message: str) -> list[TurnStreamEvent]:
        handle = await self.runner.start(self.scope, text=message, timezone="Asia/Kolkata")
        return [e async for e in handle.events()]


def _save(_: AdapterRequest) -> dict[str, object]:
    return {"intent": "save", "confidence": 0.9, "reason": "test: save"}


def _invalid(message: str) -> dict[str, Any]:
    out = heuristic_note(message)
    out["memories"][0]["entities"] = [{"entity": "e9", "role": "about"}]  # no such entity
    return out


async def test_invalid_extraction_is_retried_once_with_the_errors() -> None:
    calls: list[AdapterRequest] = []

    def extract(request: AdapterRequest) -> dict[str, Any]:
        calls.append(request)
        message = "Buy more coffee beans"
        return _invalid(message) if len(calls) == 1 else heuristic_note(message)

    setup = Setup(intent=_save, extract=extract)
    events = await setup.turn("Buy more coffee beans")

    assert isinstance(events[-1], TurnCompleted)
    assert len(calls) == 2
    retry = calls[1].messages[-1].content
    assert "entity 'e9' is not an entity id or 'self'" in retry
    assert len(setup.db.tables(setup.scope.workspace_id).items) == 1


async def test_still_invalid_extraction_fails_the_turn_cleanly_with_nothing_written() -> None:
    setup = Setup(intent=_save, extract=lambda _: _invalid("Buy more coffee beans"))
    events = await setup.turn("Buy more coffee beans")

    last = events[-1]
    assert isinstance(last, TurnFailed)
    assert last.turn.error_code == "extraction_invalid"
    tables = setup.db.tables(setup.scope.workspace_id)
    assert tables.items == {}
    assert tables.write_log == []
    stored = [e.event for e in await setup.turns.store(setup.scope).events(last.turn.id)]
    assert "memory_diff" not in [e.type for e in stored]
    error = stored[-1]
    assert isinstance(error, ErrorEvent)
    assert error.step == "extract"


async def test_a_secret_never_reaches_a_model_the_store_or_the_logs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="DEBUG", fmt="json", include_content=False)
    setup = Setup()
    events = await setup.turn(f"Remember my wifi password is {SECRET}")

    last = events[-1]
    assert isinstance(last, TurnCompleted)
    assert "didn't save that" in (last.turn.output or "")
    sent = json.dumps([[m.model_dump() for m in r.messages] for r in setup.fake.requests])
    assert SECRET not in sent  # the pre-check ran before any model saw the message
    assert SECRET not in json.dumps(setup.fake.embed_calls)
    turn = setup.turns.turns[last.turn.id]
    assert SECRET not in turn.input
    stored = [
        e.event.model_dump_json() for e in await setup.turns.store(setup.scope).events(turn.id)
    ]
    tables = setup.db.tables(setup.scope.workspace_id)
    everything = " ".join(
        [
            *stored,
            *(i.model_dump_json() for i in tables.items.values()),
            *(r.model_dump_json() for r in tables.write_log),
            capsys.readouterr().out,
        ]
    )
    assert SECRET not in everything


async def test_the_core_prefix_is_served_from_the_cache_on_the_next_turn() -> None:
    setup = Setup(
        intent=lambda _: {"intent": "chit_chat", "confidence": 1.0, "reason": "test: chat"}
    )
    first = (await setup.turn("hello"))[-1]
    second = (await setup.turn("hello again"))[-1]
    assert isinstance(first, TurnCompleted)
    assert isinstance(second, TurnCompleted)

    async def calls(turn_id: uuid.UUID) -> list[ModelCallEvent]:
        events = await setup.turns.store(setup.scope).events(turn_id)
        return [e.event for e in events if isinstance(e.event, ModelCallEvent)]

    # The very first call writes the cache; every later call with the same prefix reads it.
    first_calls, second_calls = await calls(first.turn.id), await calls(second.turn.id)
    assert first_calls[0].usage.cached_input_tokens == 0
    assert all(c.usage.cached_input_tokens > 0 for c in [*first_calls[1:], *second_calls])
    assert second.turn.usage.cached_input_tokens == sum(
        c.usage.cached_input_tokens for c in second_calls
    )


@pytest.mark.parametrize(
    "message", [f"Remember my wifi password is {SECRET}", "My gym locker combination is 17-38-02"]
)
async def test_the_trace_only_ever_holds_the_redacted_input(message: str) -> None:
    """Both paths: the pre-check (never sent) and a secret the model labelled (redacted after)."""
    setup = Setup(replay=True)
    last = (await setup.turn(message))[-1]
    assert isinstance(last, TurnCompleted)
    secret = message.rsplit(" ", 1)[-1]
    assert secret not in json.dumps(setup.tracer.sent, default=str)
    assert secret not in setup.turns.turns[last.turn.id].input


async def test_an_assumed_date_is_said_in_the_reply() -> None:
    def extract(_: AdapterRequest) -> dict[str, Any]:
        out = heuristic_note("Dentist next Friday")
        memory = out["memories"][0]
        memory.update(
            kind="plan",
            title="Dentist [[t1]]",
            text="Dentist appointment on [[t1]].",
            times=[
                {
                    "id": "t1",
                    "expression": "next Friday",
                    "clock": "occurred",
                    "direction": "future",
                    "recurring": False,
                }
            ],
        )
        return out

    setup = Setup(intent=_save, extract=extract)
    last = (await setup.turn("Dentist next Friday"))[-1]
    assert isinstance(last, TurnCompleted)
    # On a Wednesday, "next Friday" could mean in two days or in nine.
    assumption = "I took 'next Friday' as Fri 2 Oct (not Friday 25 September, the coming one)."
    assert assumption in (last.turn.output or "")


async def test_a_failed_commit_ends_the_turn_cleanly_with_nothing_written() -> None:
    setup = Setup(intent=_save)
    setup.db.faults["insert_item"] = 1
    last = (await setup.turn("Buy more coffee beans"))[-1]

    assert isinstance(last, TurnFailed)
    assert last.turn.output is None
    tables = setup.db.tables(setup.scope.workspace_id)
    assert (tables.items, tables.write_log) == ({}, [])
    stored = [e.event.type for e in await setup.turns.store(setup.scope).events(last.turn.id)]
    assert stored[-1] == "error"
    assert "memory_diff" not in stored


async def test_enrich_is_a_separate_step_that_can_be_switched_off() -> None:
    on = Setup(intent=_save)
    turn_on = (await on.turn("Buy more coffee beans"))[-1].turn  # type: ignore[union-attr]
    assert "enrich" in await on.steps(turn_on.id)
    [item] = on.db.tables(on.scope.workspace_id).items.values()
    cues = [
        k for k in await on.memory.reader(on.scope).keys([item.id]) if k.key_kind is KeyKind.CUE
    ]
    assert len(cues) <= IngestSettings().cue_keys_max

    off = Setup(intent=_save, ingest=IngestSettings(enrich_enabled=False))
    turn_off = (await off.turn("Buy more coffee beans"))[-1].turn  # type: ignore[union-attr]
    assert "enrich" not in await off.steps(turn_off.id)
    [item] = off.db.tables(off.scope.workspace_id).items.values()
    kinds = {k.key_kind for k in await off.memory.reader(off.scope).keys([item.id])}
    assert kinds == {KeyKind.TEXT, KeyKind.VERBAL}


async def test_verbal_keys_can_be_switched_off() -> None:
    setup = Setup(intent=_save, memory=MemorySettings(verbal_keys_enabled=False))
    await setup.turn("Buy more coffee beans")
    [item] = setup.db.tables(setup.scope.workspace_id).items.values()
    kinds = {k.key_kind for k in await setup.memory.reader(setup.scope).keys([item.id])}
    assert KeyKind.TEXT in kinds
    assert KeyKind.VERBAL not in kinds


async def test_undo_hides_keys_from_search_and_redo_reuses_their_embeddings() -> None:
    setup = Setup(intent=_save)
    saved = (await setup.turn("Buy more coffee beans"))[-1].turn  # type: ignore[union-attr]
    [item] = setup.db.tables(setup.scope.workspace_id).items.values()
    reader = setup.memory.reader(setup.scope)
    assert await reader.keys([item.id])

    text_key = next(k for k in await reader.keys([item.id]) if k.key_kind is KeyKind.TEXT)
    assert text_key.embedding is not None
    found = await reader.similar_items(
        text_key.embedding, limit=5, model=text_key.embedding_model or ""
    )
    assert [i for i, _ in found] == [item.id]

    undo = await setup.runner.undo(setup.scope, turn_id=saved.id, timezone="Asia/Kolkata")
    # A soft-deleted memory keeps its keys (a redo re-embeds nothing) but search skips it.
    assert (
        await reader.similar_items(
            text_key.embedding, limit=5, model=text_key.embedding_model or ""
        )
        == []
    )

    await setup.runner.undo(setup.scope, turn_id=undo.id, timezone="Asia/Kolkata")  # redo
    assert await reader.keys([item.id])
    redo = next(t for t in setup.turns.turns.values() if t.parent_turn_id == undo.id)
    events = await setup.turns.store(setup.scope).events(redo.id)
    embeds = [
        e.event for e in events if isinstance(e.event, ModelCallEvent) and e.event.step == "embed"
    ]
    assert embeds
    assert (embeds[0].cache_hits or 0) > 0  # the same sentences again: nothing re-embedded

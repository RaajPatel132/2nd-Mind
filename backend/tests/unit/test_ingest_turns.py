"""Save turns through the runner on the fake provider: invalid extraction is retried once and then
fails cleanly (S2.6), a secret never reaches a model, the store or the logs (S2.3), and the core
prefix is served from the provider cache on the next turn (S2.9)."""

import json
import uuid
from typing import Any

import pytest

from secondmind.agent import TurnCompleted, TurnFailed, TurnRunner, TurnStreamEvent
from secondmind.config import DEFAULT_RESOURCES_DIR, PromptRegistry
from secondmind.core import ErrorEvent, ModelCallEvent, WorkspaceScope
from secondmind.ingestion import offline_responders
from secondmind.ingestion.offline import heuristic_note
from secondmind.memory import Memory
from secondmind.memory.adapters import InMemoryMemory
from secondmind.observability import NullTracer, configure_logging
from secondmind.providers import AdapterRequest, FakeProvider, FakeScript
from tests.fakes import InMemoryTurns
from tests.unit.providers.helpers import router as make_router

PROMPTS = PromptRegistry.load(DEFAULT_RESOURCES_DIR / "prompts")
SECRET = "hunter2"


class Setup:
    def __init__(self, **responders: Any) -> None:
        self.fake = FakeProvider("primary", script=FakeScript(responders=offline_responders()))
        self.fake.script.responders.update(responders)
        self.turns = InMemoryTurns()
        self.db = InMemoryMemory()
        self.scope = WorkspaceScope(workspace_id=uuid.uuid4(), user_id=uuid.uuid4())
        self.runner = TurnRunner(
            router=make_router({"primary": self.fake}, "primary:m", None, all_steps=True),
            prompts=PROMPTS,
            stores=self.turns.store,
            tracer=NullTracer(),
            config_hash="c" * 64,
            max_message_chars=500,
            memory=Memory(self.db.store),
        )

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

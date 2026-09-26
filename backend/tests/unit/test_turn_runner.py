"""S1.9-S1.11: a turn runs through the graph, streams, persists typed events in order,
meters usage, and fails cleanly (never half-written)."""

import asyncio
import json
import uuid

import pytest

from secondmind.agent import (
    TokenDelta,
    TurnCompleted,
    TurnFailed,
    TurnRunner,
    TurnStarted,
    TurnStatus,
    TurnStreamEvent,
)
from secondmind.config import DEFAULT_RESOURCES_DIR, PromptRegistry
from secondmind.core import (
    ErrorEvent,
    IntentEvent,
    ModelCallEvent,
    StepEvent,
    ValidationFailedError,
    WorkspaceScope,
    new_id,
)
from secondmind.memory import Memory
from secondmind.memory.adapters import InMemoryMemory
from secondmind.observability import NullTracer, configure_logging
from secondmind.providers import FakeOutcome, FakeProvider, ModelRouter, ProviderErrorKind
from tests.fakes import InMemoryTurns
from tests.unit.providers.helpers import router as make_router


def chit_chat(_: object) -> dict[str, object]:
    return {"intent": "chit_chat", "confidence": 1.0, "reason": "test: small talk"}


PROMPTS = PromptRegistry.load(DEFAULT_RESOURCES_DIR / "prompts")
SCOPE = WorkspaceScope(workspace_id=uuid.uuid4(), user_id=uuid.uuid4())


def runner(r: ModelRouter, db: InMemoryTurns | None = None) -> tuple[TurnRunner, InMemoryTurns]:
    db = db or InMemoryTurns()
    return (
        TurnRunner(
            router=r,
            prompts=PROMPTS,
            stores=db.store,
            tracer=NullTracer(),
            config_hash="c" * 64,
            max_message_chars=100,
            memory=Memory(InMemoryMemory().store),
        ),
        db,
    )


def fake_router(
    *outcomes: FakeOutcome, fallback: bool = False
) -> tuple[ModelRouter, dict[str, FakeProvider]]:
    adapters = {"primary": FakeProvider("primary")}
    if fallback:
        adapters["backup"] = FakeProvider("backup")
    for adapter in adapters.values():
        adapter.script.responders["intent"] = chit_chat
    if outcomes:
        adapters["primary"].script.add(*outcomes, step="answer")
    r = make_router(
        adapters,  # type: ignore[arg-type]
        "primary:m",
        "backup:m" if fallback else None,
        all_steps=True,
    )
    return r, adapters


async def run(tr: TurnRunner, text: str = "hello there") -> list[TurnStreamEvent]:
    handle = await tr.start(SCOPE, text=text, timezone="Asia/Kolkata")
    return [e async for e in handle.events()]


async def test_turn_completes_with_streamed_reply_and_ordered_events() -> None:
    r, _ = fake_router(FakeOutcome(text="Hi! How can I help?"))
    tr, db = runner(r)

    events = await run(tr)

    assert isinstance(events[0], TurnStarted)
    assert isinstance(events[-1], TurnCompleted)
    tokens = "".join(e.text for e in events if isinstance(e, TokenDelta))
    turn = events[-1].turn
    assert tokens == turn.output == "Hi! How can I help?"
    assert turn.status is TurnStatus.COMPLETED
    assert turn.prompt_versions == ["intent@1", "answer@3"]
    assert turn.models["answer"].provider == "primary"
    assert turn.config_hash == "c" * 64
    assert turn.usage.total_tokens > 0
    assert turn.usage.cost_usd > 0

    stored = await db.store(SCOPE).events(turn.id)
    assert [e.seq for e in stored] == [1, 2, 3, 4, 5]
    assert [e.event.type for e in stored] == ["model_call", "intent", "step", "model_call", "step"]
    intent = stored[1].event
    assert isinstance(intent, IntentEvent)
    assert (intent.intent, intent.source) == ("chit_chat", "model")
    call = stored[3].event
    assert isinstance(call, ModelCallEvent)
    assert call.step == "answer"
    assert call.prompt == "answer@3"
    assert (
        sum(e.event.usage.total_tokens for e in stored if isinstance(e.event, ModelCallEvent))
        == turn.usage.total_tokens
    )
    # One usage-ledger row per model call, charged to the turn.
    assert [entry.step for entry in db.ledger] == ["intent", "answer"]
    assert db.ledger[0].turn_id == turn.id


async def test_fallback_is_recorded_on_the_event_and_turn() -> None:
    r, adapters = fake_router(FakeOutcome(error=ProviderErrorKind.AUTH), fallback=True)
    adapters["backup"].script.add(FakeOutcome(text="from backup"))
    tr, db = runner(r)

    events = await run(tr)

    turn = events[-1].turn
    assert turn.output == "from backup"
    assert turn.models["answer"].fallback_from == "primary:m"
    call = [e.event for e in await db.store(SCOPE).events(turn.id)][-2]
    assert isinstance(call, ModelCallEvent)
    assert call.fallback is not None
    assert "auth 401" in call.fallback.reason


async def test_provider_outage_fails_cleanly_with_a_user_message() -> None:
    r, _ = fake_router(FakeOutcome(error=ProviderErrorKind.SERVER))
    tr, db = runner(r)

    events = await run(tr)

    assert isinstance(events[-1], TurnFailed)
    turn = events[-1].turn
    assert turn.status is TurnStatus.FAILED
    assert turn.output is None
    assert turn.error_code == "provider_unavailable"
    assert turn.error_message == (
        "The model provider is unavailable right now. Please try again in a moment."
    )
    stored = [e.event for e in await db.store(SCOPE).events(turn.id)]
    assert [e.type for e in stored] == ["model_call", "intent", "step", "step", "error"]
    assert [(e.step, e.status) for e in stored if isinstance(e, StepEvent)] == [
        ("understand", "done"),
        ("answer", "failed"),
    ]
    assert isinstance(stored[-1], ErrorEvent)
    assert stored[-1].step == "answer"
    assert [entry.step for entry in db.ledger] == ["intent"]


async def test_mid_stream_failure_stores_no_partial_reply() -> None:
    r, _ = fake_router(
        FakeOutcome(text="one two three", fail_after_tokens=1, error=ProviderErrorKind.CONNECTION)
    )
    tr, _ = runner(r)

    events = await run(tr)

    assert [e.text for e in events if isinstance(e, TokenDelta)] == ["one "]
    turn = events[-1].turn
    assert isinstance(events[-1], TurnFailed)
    assert turn.output is None
    assert turn.error_code == "provider_stream_interrupted"


async def test_client_disconnect_does_not_cancel_the_turn() -> None:
    r, _ = fake_router(FakeOutcome(text="saved anyway"))
    tr, db = runner(r)

    handle = await tr.start(SCOPE, text="hello", timezone="UTC")
    first = await anext(handle.events())  # the client reads one event, then goes away
    assert isinstance(first, TurnStarted)
    await asyncio.wait_for(handle.task, timeout=5)

    turn = await db.store(SCOPE).get(handle.turn.id)
    assert turn is not None
    assert turn.status is TurnStatus.COMPLETED
    assert turn.output == "saved anyway"


async def test_history_is_sent_to_the_model() -> None:
    r, adapters = fake_router()
    tr, _ = runner(r)
    await run(tr, "my first message")
    await run(tr, "my second message")

    request = adapters["primary"].requests[-1]
    assert [m.role for m in request.messages] == ["user", "assistant", "user"]
    assert request.messages[0].content == "my first message"
    assert request.system is not None
    assert "Asia/Kolkata" in request.system


@pytest.mark.parametrize(("text", "error"), [("   ", "empty"), ("x" * 101, "longer than 100")])
async def test_invalid_messages_are_rejected_before_a_turn_exists(text: str, error: str) -> None:
    r, _ = fake_router()
    tr, db = runner(r)
    with pytest.raises(ValidationFailedError, match=error):
        await tr.start(SCOPE, text=text, timezone="UTC")
    assert db.turns == {}


async def test_logs_carry_turn_id_but_never_message_content(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="INFO", fmt="json", include_content=False)
    marker = f"secret-marker-{new_id()}"
    r, _ = fake_router(FakeOutcome(text=f"echo {marker}"))
    tr, _ = runner(r)

    events = await run(tr, f"please remember {marker}")

    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert marker not in out
    finished = [line for line in lines if line["event"] == "turn.finished"]
    assert finished
    assert finished[0]["turn_id"] == str(events[-1].turn.id)

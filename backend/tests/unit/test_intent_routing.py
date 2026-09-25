"""S2.4: the turn graph branches on the intent. The user never picks a mode.

With the fake provider every intent is checked end to end. ``pytest -m live -s`` runs the
labelled set in ``tests/fixtures/intent.yaml`` against the configured intent model and prints
the accuracy: a baseline for the sprint report, not a gate.
"""

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

from secondmind.agent import (
    CORRECT_STUB,
    RECALL_STUB,
    IntentPromptVars,
    TurnCompleted,
    TurnRunner,
)
from secondmind.config import DEFAULT_RESOURCES_DIR, PromptRegistry, Step
from secondmind.core import ConfigError, WorkspaceScope
from secondmind.evals.ingest import live_router
from secondmind.ingestion import IntentOutput, offline_responders
from secondmind.memory import Memory
from secondmind.memory.adapters import InMemoryMemory
from secondmind.observability import NullTracer
from secondmind.providers import ChatMessage, FakeProvider, FakeScript
from tests.fakes import InMemoryTurns
from tests.unit.providers.helpers import router as make_router

PROMPTS = PromptRegistry.load(DEFAULT_RESOURCES_DIR / "prompts")
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "intent.yaml"


async def _turn(intent: str, message: str) -> tuple[str, list[str]]:
    fake = FakeProvider("primary", script=FakeScript(responders=offline_responders()))

    def decide(_: Any) -> dict[str, object]:
        return {"intent": intent, "confidence": 0.9, "reason": f"test: {intent}"}

    fake.script.responders["intent"] = decide
    turns = InMemoryTurns()
    scope = WorkspaceScope(workspace_id=uuid.uuid4(), user_id=uuid.uuid4())
    runner = TurnRunner(
        router=make_router({"primary": fake}, "primary:m", None, all_steps=True),
        prompts=PROMPTS,
        stores=turns.store,
        tracer=NullTracer(),
        config_hash="c" * 64,
        max_message_chars=500,
        memory=Memory(InMemoryMemory().store),
    )
    handle = await runner.start(scope, text=message, timezone="Asia/Kolkata")
    last = [e async for e in handle.events()][-1]
    assert isinstance(last, TurnCompleted), last
    events: list[str] = [e.event.type for e in await turns.store(scope).events(last.turn.id)]
    return last.turn.output or "", events


async def test_save_runs_ingestion_and_writes_memory() -> None:
    reply, events = await _turn("save", "Spare keys are in the kitchen cabinet")
    assert reply.startswith("Saved")
    assert "decision" in events
    assert "memory_diff" in events


async def test_recall_gets_the_honest_stub_and_writes_nothing() -> None:
    reply, events = await _turn("recall", "Where are the spare keys?")
    assert reply == RECALL_STUB
    assert "memory_diff" not in events


async def test_save_and_recall_saves_then_adds_the_recall_stub() -> None:
    reply, events = await _turn("save_and_recall", "Finished Mindhunter. What else is on my list?")
    assert reply.startswith("Saved")
    assert reply.endswith(RECALL_STUB)
    assert "memory_diff" in events


async def test_correct_gets_its_stub_and_writes_nothing() -> None:
    reply, events = await _turn("correct", "No, Nisha is my cousin")
    assert reply == CORRECT_STUB
    assert "memory_diff" not in events


async def test_chit_chat_is_answered_by_the_answer_step() -> None:
    reply, events = await _turn("chit_chat", "hello!")
    assert "fake provider" in reply
    assert events == ["model_call", "intent", "model_call"]


@pytest.mark.live
async def test_intent_accuracy_baseline(capsys: pytest.CaptureFixture[str]) -> None:
    try:
        router = live_router()
    except ConfigError as exc:
        pytest.skip(exc.message)
    route = router.route(Step.INTENT)
    assert route.prompt is not None
    system = PROMPTS.render(
        route.prompt, IntentPromptVars(now="2026-09-23T10:00", timezone="Asia/Kolkata")
    ).text
    cases = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    misses: list[str] = []
    try:
        for case in cases:
            result = await router.structured(
                Step.INTENT,
                IntentOutput,
                system=system,
                messages=[ChatMessage.user(case["message"])],
                prompt=route.prompt,
            )
            if result.value.intent != case["intent"]:
                misses.append(f"{case['message']!r}: {result.value.intent} ≠ {case['intent']}")
    finally:
        await router.aclose()
    right = len(cases) - len(misses)
    with capsys.disabled():
        sys.stdout.write(
            f"\nintent accuracy ({route.primary}): {right}/{len(cases)} "
            f"= {100 * right / len(cases):.0f}%\n"
        )
        sys.stdout.writelines(f"  miss {m}\n" for m in misses)

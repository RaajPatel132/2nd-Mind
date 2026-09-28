"""S2.4: the turn graph branches on the intent. The user never picks a mode.

With the fake provider every intent is checked end to end. Accuracy on the labelled messages is
an eval suite (``make eval-intent`` / ``eval-intent-live``), not a test.
"""

import uuid
from pathlib import Path
from typing import Any

from secondmind.agent import (
    TurnCompleted,
    TurnRunner,
)
from secondmind.config import DEFAULT_RESOURCES_DIR, PromptRegistry
from secondmind.core import WorkspaceScope
from secondmind.corrections import complete_correction, correction_responders
from secondmind.ingestion import offline_responders
from secondmind.memory import Memory
from secondmind.memory.adapters import InMemoryMemory
from secondmind.observability import NullTracer
from secondmind.providers import FakeProvider, FakeScript
from secondmind.retrieval import recall_responders, recall_text_responders
from tests.fakes import InMemoryTurns
from tests.unit.providers.helpers import router as make_router

PROMPTS = PromptRegistry.load(DEFAULT_RESOURCES_DIR / "prompts")
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "intent.yaml"


async def _turn(
    intent: str, message: str, correction: dict[str, Any] | None = None
) -> tuple[str, list[str]]:
    fake = FakeProvider(
        "primary",
        script=FakeScript(
            responders=offline_responders() | recall_responders() | correction_responders(),
            text_responders=recall_text_responders(),
        ),
    )

    def decide(_: Any) -> dict[str, object]:
        return {"intent": intent, "confidence": 0.9, "reason": f"test: {intent}"}

    fake.script.responders["intent"] = decide
    if correction is not None:
        fake.script.responders["correct"] = lambda _: complete_correction(correction)
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


async def test_recall_runs_the_pipeline_and_writes_nothing() -> None:
    reply, events = await _turn("recall", "Where are the spare keys?")
    # No store is wired, so nothing is found: the template says so, with no answer model call.
    assert reply == "I don't have anything saved about Where are the spare keys?."
    assert "retrieval" in events
    assert "citations" in events
    assert "memory_diff" not in events


async def test_save_and_recall_saves_then_recalls() -> None:
    reply, events = await _turn("save_and_recall", "Finished Mindhunter. What else is on my list?")
    assert reply.startswith("Saved")
    assert "I don't have anything saved about" in reply
    assert events.index("memory_diff") < events.index("retrieval")


async def test_correct_runs_the_corrector_and_writes_nothing_it_cannot_work_out() -> None:
    reply, events = await _turn("correct", "No, Nisha is my cousin")
    assert "offline fake can't work out corrections" in reply
    assert "memory_diff" not in events


async def test_chit_chat_is_answered_by_the_answer_step() -> None:
    reply, events = await _turn("chit_chat", "hello!")
    assert "fake provider" in reply
    assert events == ["model_call", "intent", "step", "model_call", "step"]


async def test_new_information_taken_for_a_correction_is_saved() -> None:
    """Found live (R.3 B2): "I moved to Pune" routed to the corrector, which said it wasn't a
    correction, and the message was lost. Now the turn goes on to save it."""
    reply, events = await _turn(
        "correct",
        "Spare keys are in the kitchen cabinet now",
        {"type": "not_a_correction", "reason": "New information about where the keys are."},
    )
    assert reply.startswith("Saved")
    assert "memory_diff" in events

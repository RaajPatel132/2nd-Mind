"""The turn graph (LangGraph): ``intent`` routes to ``ingest``, the recall/correct stubs or
``answer`` (S2.4). The user never picks a mode.

The graph stays thin: nodes read their collaborators from the runtime context and call plain
modules. Reply text leaves through ``TurnContext.write`` and step progress through
``TurnContext.trail``, which feed one queue, so the client sees them in the order they happened.
"""

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel

from secondmind.config import PromptRegistry, Step
from secondmind.core import AgentStep, Intent, IntentEvent, NullTrail, Trail, TurnEvent
from secondmind.ingestion import IngestOutcome, IntentOutput, ModelSteps
from secondmind.observability import GenerationSpan, TurnTrace
from secondmind.providers import (
    ChatMessage,
    ChatResult,
    ModelCall,
    ModelRouter,
    ProviderUnavailableError,
    TextDelta,
)

RECALL_STUB = (
    "I can't look things up in your memory yet: recall isn't wired up, so I won't guess. "
    "Saving works, though; tell me anything and I'll keep it."
)
CORRECT_STUB = (
    "Correcting saved memories by chat isn't wired up yet. For now, open that turn's glass box "
    "and undo it, then tell me the right version."
)


class AnswerPromptVars(BaseModel):
    """Typed variables of the ``answer`` prompt."""

    now: str
    timezone: str


class IntentPromptVars(BaseModel):
    now: str
    timezone: str


class TurnState(TypedDict):
    message: str
    intent: NotRequired[str]
    answer: NotRequired[str]
    secret_values: NotRequired[list[str]]


RecordModelCall = Callable[[ModelCall, GenerationSpan, object, str], Awaitable[None]]


def _discard(_: str) -> None:
    return None


@dataclass(frozen=True, slots=True)
class TurnContext:
    """Per-turn collaborators, passed to nodes as LangGraph runtime context."""

    router: ModelRouter
    prompts: PromptRegistry
    trace: TurnTrace
    now: datetime
    timezone: str
    history: Sequence[ChatMessage]
    emit: Callable[[TurnEvent], Awaitable[None]]
    record_model_call: RecordModelCall
    steps: ModelSteps
    ingest: Callable[[], Awaitable[IngestOutcome]]
    secret_found: bool = False
    core_prefix: str | None = None
    trail: Trail = field(default_factory=NullTrail)
    write: Callable[[str], None] = _discard


def _stream(runtime: Runtime[TurnContext], text: str) -> None:
    for piece in re.findall(r"\S+\s*|\s+", text):
        runtime.context.write(piece)


async def intent_node(state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
    """Decide what the message is for (FR-1.2). A secret skips the model entirely."""
    ctx = runtime.context
    async with ctx.trail.run(AgentStep.UNDERSTAND):
        if ctx.secret_found:
            event = IntentEvent(
                intent=Intent.SAVE,
                confidence=1.0,
                reason="The secret pre-check matched, so no model saw this message.",
                source="rule",
            )
        else:
            out = await ctx.steps.structured(
                Step.INTENT,
                IntentOutput,
                IntentPromptVars(now=ctx.now.isoformat(timespec="minutes"), timezone=ctx.timezone),
                [ChatMessage.user(state["message"])],
            )
            event = IntentEvent(
                intent=Intent(out.intent),
                confidence=min(max(out.confidence, 0.0), 1.0),
                reason=out.reason,
                source="model",
            )
        await ctx.emit(event)
    return {"intent": event.intent.value}


def route_intent(state: TurnState) -> str:
    intent = state.get("intent")
    if intent in (Intent.SAVE, Intent.SAVE_AND_RECALL):
        return "ingest"
    if intent == Intent.RECALL:
        return "recall_stub"
    if intent == Intent.CORRECT:
        return "correct_stub"
    return "answer"


async def ingest_node(state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
    outcome = await runtime.context.ingest()
    async with runtime.context.trail.run(AgentStep.ANSWER):
        _stream(runtime, outcome.reply)
    return {"answer": outcome.reply, "secret_values": outcome.secret_values}


def after_ingest(state: TurnState) -> str:
    return "recall_stub" if state.get("intent") == Intent.SAVE_AND_RECALL else END


async def recall_stub_node(state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
    before = state.get("answer")
    if before:  # after a save: the same reply goes on, in the answer step that already ran
        text = f"\n\n{RECALL_STUB}"
        _stream(runtime, text)
    else:
        text = RECALL_STUB
        async with runtime.context.trail.run(AgentStep.ANSWER):
            _stream(runtime, text)
    return {"answer": (before or "") + text}


async def correct_stub_node(state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
    async with runtime.context.trail.run(AgentStep.ANSWER):
        _stream(runtime, CORRECT_STUB)
    return {"answer": CORRECT_STUB}


async def answer_node(state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
    ctx = runtime.context
    route = ctx.router.route(Step.ANSWER)
    if route.prompt is None:
        raise RuntimeError("the answer step has no prompt configured")
    local_now = ctx.now.isoformat(timespec="minutes")
    system = ctx.prompts.render(
        route.prompt, AnswerPromptVars(now=local_now, timezone=ctx.timezone)
    ).text
    messages = [*ctx.history, ChatMessage.user(state["message"])]
    async with ctx.trail.run(AgentStep.ANSWER):
        span = ctx.trace.start_generation(Step.ANSWER.value)
        result: ChatResult | None = None
        try:
            async for event in ctx.router.stream(
                Step.ANSWER,
                system=system,
                messages=messages,
                prompt=route.prompt,
                cache_prefix=ctx.core_prefix,
            ):
                if isinstance(event, TextDelta):
                    ctx.write(event.text)
                else:
                    result = event
        except ProviderUnavailableError as exc:
            span.fail(exc.detail)
            raise
        if result is None:
            span.fail("stream ended without a result")
            raise RuntimeError("answer stream ended without a result")
        await ctx.record_model_call(
            result.call, span, [m.model_dump() for m in messages], result.text
        )
    return {"answer": result.text}


def build_turn_graph() -> CompiledStateGraph[TurnState, TurnContext, TurnState, TurnState]:
    graph = StateGraph(TurnState, context_schema=TurnContext)
    graph.add_node("intent", intent_node)
    graph.add_node("ingest", ingest_node)
    graph.add_node("recall_stub", recall_stub_node)
    graph.add_node("correct_stub", correct_stub_node)
    graph.add_node("answer", answer_node)
    graph.add_edge(START, "intent")
    graph.add_conditional_edges(
        "intent",
        route_intent,
        {
            "ingest": "ingest",
            "recall_stub": "recall_stub",
            "correct_stub": "correct_stub",
            "answer": "answer",
        },
    )
    graph.add_conditional_edges("ingest", after_ingest, {"recall_stub": "recall_stub", END: END})
    graph.add_edge("recall_stub", END)
    graph.add_edge("correct_stub", END)
    graph.add_edge("answer", END)
    return graph.compile()

"""The turn graph (LangGraph): ``intent`` routes to ``ingest`` (save), ``recall``, ``correct``
or ``answer`` (chit-chat); ``save_and_recall`` saves first and recall then sees the save.
Every turn ends with the trigger check (S3.10). The user never picks a mode.

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
from secondmind.corrections import CorrectOutcome
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
from secondmind.retrieval import AnswerVars, RecallOutcome, chit_chat_context

AnswerPromptVars = AnswerVars


class IntentPromptVars(BaseModel):
    now: str
    timezone: str


class TurnState(TypedDict):
    message: str
    intent: NotRequired[str]
    answer: NotRequired[str]
    secret_values: NotRequired[list[str]]
    # The message's embedding, when recall made one (the trigger check reuses it).
    vector: NotRequired[list[float] | None]


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
    recall: Callable[[bool], Awaitable[RecallOutcome]] | None = None
    triggers: Callable[[list[float] | None], Awaitable[str | None]] | None = None
    correct: Callable[[], Awaitable[CorrectOutcome]] | None = None
    # A "yes" to the offer the previous reply made: routed to correct by rule, no model call.
    accepts_offer: bool = False
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
        if ctx.accepts_offer and not ctx.secret_found:
            event = IntentEvent(
                intent=Intent.CORRECT,
                confidence=1.0,
                reason="A yes to the offer in the previous reply, so it applies that fix.",
                source="rule",
            )
        elif ctx.secret_found:
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
        return "recall"
    if intent == Intent.CORRECT:
        return "correct"
    return "answer"


async def ingest_node(state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
    outcome = await runtime.context.ingest()
    async with runtime.context.trail.run(AgentStep.ANSWER):
        _stream(runtime, outcome.reply)
    return {"answer": outcome.reply, "secret_values": outcome.secret_values}


def after_ingest(state: TurnState) -> str:
    return "recall" if state.get("intent") == Intent.SAVE_AND_RECALL else "triggers"


async def recall_node(state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
    """Recall (S3.4-S3.8). After a save the reply goes on; the save is already committed, so
    recall sees it ("Add Dark to my watchlist. What else is on it?" lists Dark)."""
    ctx = runtime.context
    if ctx.recall is None:
        raise RuntimeError("recall is not configured")
    before = state.get("answer")
    if before:
        ctx.write("\n\n")
    outcome = await ctx.recall(bool(before))
    answer = f"{before}\n\n{outcome.reply}" if before else outcome.reply
    return {"answer": answer, "vector": outcome.message_vector}


async def triggers_node(state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
    """Every turn: pending person and topic triggers that match fire, and the reply ends with
    their note (S3.10)."""
    ctx = runtime.context
    if ctx.triggers is None:
        return {}
    note = await ctx.triggers(state.get("vector"))
    if not note:
        return {}
    text = f"\n\n{note}"
    _stream(runtime, text)
    return {"answer": (state.get("answer") or "") + text}


async def correct_node(state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
    """A correction by chat (S3.12): reclassify, correct a wrong value, forget, or re-file."""
    ctx = runtime.context
    if ctx.correct is None:
        raise RuntimeError("corrections are not configured")
    outcome = await ctx.correct()
    return {"answer": outcome.reply}


async def answer_node(state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
    ctx = runtime.context
    route = ctx.router.route(Step.ANSWER)
    if route.prompt is None:
        raise RuntimeError("the answer step has no prompt configured")
    local_now = ctx.now.isoformat(timespec="minutes")
    system = ctx.prompts.render(
        route.prompt,
        AnswerPromptVars(now=local_now, timezone=ctx.timezone, context=chit_chat_context()),
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
    graph.add_node("recall", recall_node)
    graph.add_node("correct", correct_node)
    graph.add_node("answer", answer_node)
    graph.add_node("triggers", triggers_node)
    graph.add_edge(START, "intent")
    graph.add_conditional_edges(
        "intent",
        route_intent,
        {
            "ingest": "ingest",
            "recall": "recall",
            "correct": "correct",
            "answer": "answer",
        },
    )
    graph.add_conditional_edges(
        "ingest", after_ingest, {"recall": "recall", "triggers": "triggers"}
    )
    graph.add_edge("recall", "triggers")
    graph.add_edge("correct", "triggers")
    graph.add_edge("answer", "triggers")
    graph.add_edge("triggers", END)
    return graph.compile()

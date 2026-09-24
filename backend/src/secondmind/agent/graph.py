"""The turn graph (LangGraph): ``intent`` -> ``answer``. The shape S2 and S3 plug into.

The graph stays thin: nodes read their collaborators from the runtime context and call plain
modules. Tokens leave the graph through LangGraph's custom stream writer.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel

from secondmind.config import PromptRegistry, Step
from secondmind.core import Intent, IntentEvent, TurnEvent
from secondmind.observability import GenerationSpan, TurnTrace
from secondmind.providers import (
    ChatMessage,
    ChatResult,
    ModelCall,
    ModelRouter,
    ProviderUnavailableError,
    TextDelta,
)


class AnswerPromptVars(BaseModel):
    """Typed variables of the ``answer`` prompt."""

    now: str
    timezone: str


class TurnState(TypedDict):
    message: str
    intent: NotRequired[str]
    answer: NotRequired[str]


RecordModelCall = Callable[[ModelCall, GenerationSpan, object, str], Awaitable[None]]


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


async def intent_node(state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
    """Stub until S2: every message is chit-chat."""
    event = IntentEvent(
        intent=Intent.CHIT_CHAT,
        confidence=1.0,
        reason="Stub intent router: every message is chit-chat until intent detection (S2).",
        source="stub",
    )
    await runtime.context.emit(event)
    return {"intent": event.intent.value}


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
    span = ctx.trace.start_generation(Step.ANSWER.value)
    result: ChatResult | None = None
    try:
        async for event in ctx.router.stream(
            Step.ANSWER, system=system, messages=messages, prompt=route.prompt
        ):
            if isinstance(event, TextDelta):
                runtime.stream_writer(event.text)
            else:
                result = event
    except ProviderUnavailableError as exc:
        span.fail(exc.detail)
        raise
    if result is None:
        span.fail("stream ended without a result")
        raise RuntimeError("answer stream ended without a result")
    await ctx.record_model_call(result.call, span, [m.model_dump() for m in messages], result.text)
    return {"answer": result.text}


def build_turn_graph() -> CompiledStateGraph[TurnState, TurnContext, TurnState, TurnState]:
    graph = StateGraph(TurnState, context_schema=TurnContext)
    graph.add_node("intent", intent_node)
    graph.add_node("answer", answer_node)
    graph.add_edge(START, "intent")
    graph.add_edge("intent", "answer")
    graph.add_edge("answer", END)
    return graph.compile()

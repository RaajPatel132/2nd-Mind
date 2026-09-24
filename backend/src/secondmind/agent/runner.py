"""Runs one turn end to end and streams its progress.

The turn row is created before streaming starts (so the client always gets a turn id). The
graph then runs in its own task: a client that disconnects mid-reply does not cancel the turn,
which finishes and is saved. Events are persisted as they happen, in order; every model call
writes its ``model_call`` event and usage-ledger row together. A turn always ends with a
typed status: ``completed`` with its reply, or ``failed`` with a user-safe message and an
``error`` event. A failed turn stores no reply (nothing half-written, NFR-6.2).
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from secondmind.agent.graph import TurnContext, build_turn_graph
from secondmind.agent.turns import (
    StepModel,
    TraceStatus,
    Turn,
    TurnOutcome,
    TurnStatus,
    TurnStore,
    TurnStoreFactory,
)
from secondmind.config import PromptRegistry
from secondmind.core import (
    Clock,
    ErrorEvent,
    TurnEvent,
    UsageTotals,
    ValidationFailedError,
    WorkspaceScope,
    new_id,
    utc_now,
)
from secondmind.observability import (
    GenerationSpan,
    Tracer,
    TurnTrace,
    bind_log_context,
    get_logger,
)
from secondmind.providers import ChatMessage, ModelCall, ModelRouter, ProviderUnavailableError

log = get_logger(__name__)

INTERNAL_ERROR_MESSAGE = "Something went wrong on our side, and this turn was not saved."


@dataclass(frozen=True, slots=True)
class TurnStarted:
    turn: Turn


@dataclass(frozen=True, slots=True)
class TokenDelta:
    text: str


@dataclass(frozen=True, slots=True)
class TurnCompleted:
    turn: Turn


@dataclass(frozen=True, slots=True)
class TurnFailed:
    turn: Turn


TurnStreamEvent = TurnStarted | TokenDelta | TurnCompleted | TurnFailed


@dataclass(slots=True)
class TurnHandle:
    turn: Turn
    task: asyncio.Task[None]
    _queue: asyncio.Queue[TurnStreamEvent | None]

    async def events(self) -> AsyncIterator[TurnStreamEvent]:
        while (event := await self._queue.get()) is not None:
            yield event

    async def next_event(self, timeout_s: float) -> TurnStreamEvent | None:
        """The next event, or None once the turn is over. Raises TimeoutError if nothing
        arrives in time (safe to call again: waiting on the queue is cancellation-safe)."""
        return await asyncio.wait_for(self._queue.get(), timeout_s)


@dataclass(slots=True)
class _Tally:
    usage: UsageTotals = field(default_factory=UsageTotals)
    models: dict[str, StepModel] = field(default_factory=dict)
    prompts: list[str] = field(default_factory=list)

    def add(self, call: ModelCall) -> None:
        self.usage = self.usage.add(call.usage)
        fallback_from = None
        if call.fallback is not None:
            fallback_from = f"{call.fallback.from_provider}:{call.fallback.from_model}"
        self.models[call.step] = StepModel(
            provider=call.provider,
            model=call.model,
            prompt=call.prompt,
            fallback_from=fallback_from,
        )
        if call.prompt and call.prompt not in self.prompts:
            self.prompts.append(call.prompt)


@dataclass(slots=True)
class _TurnRun:
    """Everything one running turn needs, passed between the runner's steps."""

    scope: WorkspaceScope
    store: TurnStore
    turn: Turn
    history: list[ChatMessage]
    timezone: str
    queue: asyncio.Queue[TurnStreamEvent | None]
    tally: _Tally = field(default_factory=_Tally)
    trace: TurnTrace | None = None


class TurnRunner:
    def __init__(
        self,
        *,
        router: ModelRouter,
        prompts: PromptRegistry,
        stores: TurnStoreFactory,
        tracer: Tracer,
        config_hash: str,
        max_message_chars: int,
        history_turns: int = 10,
        clock: Clock = utc_now,
    ) -> None:
        self._router = router
        self._prompts = prompts
        self._stores = stores
        self._tracer = tracer
        self._config_hash = config_hash
        self._max_chars = max_message_chars
        self._history_turns = history_turns
        self._clock = clock
        self._graph = build_turn_graph()
        self._tasks: set[asyncio.Task[None]] = set()

    def store(self, scope: WorkspaceScope) -> TurnStore:
        return self._stores(scope)

    async def start(self, scope: WorkspaceScope, *, text: str, timezone: str) -> TurnHandle:
        message = text.strip()
        if not message:
            raise ValidationFailedError("message is empty")
        if len(message) > self._max_chars:
            raise ValidationFailedError(f"message is longer than {self._max_chars} characters")
        store = self._stores(scope)
        history = await self._history(store)
        started = self._clock()
        turn = await store.create(
            turn_id=new_id(), text=message, config_hash=self._config_hash, started_at=started
        )
        queue: asyncio.Queue[TurnStreamEvent | None] = asyncio.Queue()
        await queue.put(TurnStarted(turn))
        run = _TurnRun(
            scope=scope, store=store, turn=turn, history=history, timezone=timezone, queue=queue
        )
        task = asyncio.create_task(self._run(run), name=f"turn-{turn.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return TurnHandle(turn=turn, task=task, _queue=queue)

    async def aclose(self, timeout_s: float = 10.0) -> None:
        """Let running turns finish (bounded), then cancel the rest (they end as failed)."""
        if not self._tasks:
            return
        _, pending = await asyncio.wait(set(self._tasks), timeout=timeout_s)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.wait(pending, timeout=5)

    async def _history(self, store: TurnStore) -> list[ChatMessage]:
        turns = await store.recent(limit=self._history_turns)
        history: list[ChatMessage] = []
        for past in reversed(turns):
            if past.status is TurnStatus.COMPLETED and past.output:
                history.append(ChatMessage.user(past.input))
                history.append(ChatMessage(role="assistant", content=past.output))
        return history

    async def _run(self, run: _TurnRun) -> None:
        turn, store = run.turn, run.store
        bind_log_context(turn_id=str(turn.id), workspace_id=str(run.scope.workspace_id))
        trace = self._tracer.start_turn(
            turn_id=turn.id,
            workspace_id=run.scope.workspace_id,
            user_id=run.scope.user_id,
            turn_input=turn.input,
            metadata={"config_hash": self._config_hash},
        )
        run.trace = trace

        async def emit(event: TurnEvent) -> None:
            await store.append(turn.id, event)

        async def record(
            call: ModelCall, span: GenerationSpan, prompt_input: object, output: str
        ) -> None:
            event = call.to_event()
            await store.record_model_call(turn.id, event)
            run.tally.add(call)
            span.finish(event, prompt_input=prompt_input, output=output)

        context = TurnContext(
            router=self._router,
            prompts=self._prompts,
            trace=trace,
            now=turn.started_at.astimezone(ZoneInfo(run.timezone)),
            timezone=run.timezone,
            history=run.history,
            emit=emit,
            record_model_call=record,
        )
        answer: str | None = None
        error: ErrorEvent | None = None
        log.info("turn.started")
        try:
            async for mode, chunk in self._graph.astream(
                {"message": turn.input}, context=context, stream_mode=["custom", "values"]
            ):
                if mode == "custom":
                    await run.queue.put(TokenDelta(str(chunk)))
                elif isinstance(chunk, dict) and chunk.get("answer") is not None:
                    answer = str(chunk["answer"])
            if answer is None:
                raise RuntimeError("turn graph finished without an answer")
        except ProviderUnavailableError as exc:
            log.warning("turn.provider_unavailable", step=exc.step, attempts=exc.detail)
            error = ErrorEvent(code=exc.code, message=exc.message, step=exc.step, retryable=True)
        except asyncio.CancelledError:
            error = ErrorEvent(code="cancelled", message=INTERNAL_ERROR_MESSAGE)
            await asyncio.shield(self._finish(run, None, error))
            raise
        except Exception:
            log.exception("turn.crashed")
            error = ErrorEvent(code="internal_error", message=INTERNAL_ERROR_MESSAGE)
        await self._finish(run, answer, error)

    async def _finish(self, run: _TurnRun, answer: str | None, error: ErrorEvent | None) -> None:
        turn, store = run.turn, run.store
        try:
            if error is not None:
                with contextlib.suppress(Exception):
                    await store.append(turn.id, error)
            outcome = TurnOutcome(
                status=TurnStatus.FAILED if error else TurnStatus.COMPLETED,
                output=None if error else answer,
                usage=run.tally.usage,
                models=run.tally.models,
                prompt_versions=run.tally.prompts,
                trace_status=await self._trace_status(),
                finished_at=self._clock(),
                error_code=error.code if error else None,
                error_message=error.message if error else None,
            )
            try:
                final = await store.finish(turn.id, outcome)
            except Exception:
                log.exception("turn.finish_failed")
                final = turn.model_copy(
                    update={
                        "status": TurnStatus.FAILED,
                        "error_code": "internal_error",
                        "error_message": INTERNAL_ERROR_MESSAGE,
                    }
                )
            done: TurnStreamEvent = (
                TurnCompleted(final) if final.status is TurnStatus.COMPLETED else TurnFailed(final)
            )
            # The client gets its terminal event first; tracing and logging come after and
            # can never take it away.
            await run.queue.put(done)
            try:
                if run.trace is not None:
                    run.trace.finish(
                        status=final.status.value, output=final.output, error=final.error_code
                    )
                log.info(
                    "turn.finished",
                    status=final.status.value,
                    tokens=final.usage.total_tokens,
                    cost_usd=float(final.usage.cost_usd),
                    error_code=final.error_code,
                )
            except Exception:
                log.exception("turn.after_finish_failed")
        finally:
            await run.queue.put(None)

    async def _trace_status(self) -> TraceStatus:
        if not self._tracer.enabled:
            return TraceStatus.DISABLED
        return TraceStatus.RECORDED if await self._tracer.available() else TraceStatus.UNAVAILABLE

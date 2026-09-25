"""Runs one turn end to end and streams its progress.

The turn row is created before streaming starts (so the client always gets a turn id). The
graph then runs in its own task: a client that disconnects mid-reply does not cancel the turn,
which finishes and is saved. Events are persisted as they happen, in order; every model call
writes its ``model_call`` event and usage-ledger row together. A turn always ends with a
typed status: ``completed`` with its reply, or ``failed`` with a user-safe message and an
``error`` event. A failed turn stores no reply and no memory writes (NFR-6.1, NFR-6.2).

The secret pre-check runs before the turn row is created, so a secret never reaches the
database, the trace backend or a model provider (ADR-0021).

Undo, confirming a held write and background jobs run as turns too (``TurnKind``), with their
own events and diff, so they are auditable and can themselves be undone.
"""

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from secondmind.agent.graph import TurnContext, build_turn_graph
from secondmind.agent.trail import EventRecorded, StepStarted, TurnTrail
from secondmind.agent.turns import (
    StepModel,
    TraceStatus,
    Turn,
    TurnKind,
    TurnOutcome,
    TurnStatus,
    TurnStore,
    TurnStoreFactory,
)
from secondmind.config import PromptRegistry
from secondmind.core import (
    AgentStep,
    Clock,
    ErrorEvent,
    ModelCallEvent,
    TurnEvent,
    UsageTotals,
    ValidationFailedError,
    WorkspaceScope,
    new_id,
    utc_now,
)
from secondmind.ingestion import (
    ExtractionInvalidError,
    IngestContext,
    IngestionPipeline,
    IngestOutcome,
    IngestSettings,
    ModelSteps,
    TurnNow,
    summarise_commit,
)
from secondmind.memory import CommitResult, Embedder, Memory, WriterTurn
from secondmind.observability import (
    GenerationSpan,
    Tracer,
    TurnTrace,
    bind_log_context,
    get_logger,
)
from secondmind.policy import redact_values, scan_secrets
from secondmind.providers import ChatMessage, ModelCall, ModelRouter, ProviderUnavailableError

log = get_logger(__name__)

INTERNAL_ERROR_MESSAGE = "Something went wrong on our side, and this turn was not saved."

# Called after a commit that renamed or relabelled entities (their items' keys need
# re-rendering in the background, as a system turn).
EntitiesRenamed = Callable[[WorkspaceScope, set[uuid.UUID]], Awaitable[None]]


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


TurnStreamEvent = (
    TurnStarted | TokenDelta | StepStarted | EventRecorded | TurnCompleted | TurnFailed
)


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
    queue: asyncio.Queue[TurnStreamEvent | None] | None
    default_lead_minutes: int = 1440
    secret_kinds: list[str] = field(default_factory=list)
    tally: _Tally = field(default_factory=_Tally)
    trace: TurnTrace | None = None
    trail: TurnTrail | None = None
    redacted_input: str | None = None
    # Secret values found during the turn, and generations waiting to be sent to the trace:
    # a secret the model labels late must not reach the trace through an earlier call.
    secret_values: list[str] = field(default_factory=list)
    generations: list[tuple[GenerationSpan, ModelCallEvent, object, str]] = field(
        default_factory=list
    )


# An action run as a non-chat turn (undo, confirm, system job): given the writer turn, the
# event sink and the model steps, it commits memory changes and returns the reply.
TurnAction = Callable[
    [WriterTurn, Callable[[TurnEvent], Awaitable[None]], ModelSteps], Awaitable[str]
]


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
        memory: Memory,
        ingest: IngestSettings | None = None,
        embed_dimensions: int = 1536,
        on_entities_renamed: EntitiesRenamed | None = None,
        history_turns: int = 10,
        clock: Clock = utc_now,
    ) -> None:
        self._router = router
        self._prompts = prompts
        self._stores = stores
        self._tracer = tracer
        self._config_hash = config_hash
        self._max_chars = max_message_chars
        self._memory = memory
        self._pipeline = IngestionPipeline(ingest)
        self._embed_dimensions = embed_dimensions
        self._on_renamed = on_entities_renamed
        self._history_turns = history_turns
        self._clock = clock
        self._graph = build_turn_graph()
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def memory(self) -> Memory:
        return self._memory

    def store(self, scope: WorkspaceScope) -> TurnStore:
        return self._stores(scope)

    async def start(
        self,
        scope: WorkspaceScope,
        *,
        text: str,
        timezone: str,
        default_lead_minutes: int = 1440,
    ) -> TurnHandle:
        message = text.strip()
        if not message:
            raise ValidationFailedError("message is empty")
        if len(message) > self._max_chars:
            raise ValidationFailedError(f"message is longer than {self._max_chars} characters")
        # Before anything is stored or sent anywhere: a secret never leaves this function.
        scan = scan_secrets(message)
        store = self._stores(scope)
        history = await self._history(store)
        started = self._clock()
        turn = await store.create(
            turn_id=new_id(), text=scan.redacted, config_hash=self._config_hash, started_at=started
        )
        queue: asyncio.Queue[TurnStreamEvent | None] = asyncio.Queue()
        await queue.put(TurnStarted(turn))
        run = _TurnRun(
            scope=scope,
            store=store,
            turn=turn,
            history=history,
            timezone=timezone,
            queue=queue,
            default_lead_minutes=default_lead_minutes,
            secret_kinds=scan.kinds,
        )
        run.trail = TurnTrail(store, turn.id, queue.put, clock=self._clock)
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
            if past.kind is TurnKind.USER and past.status is TurnStatus.COMPLETED and past.output:
                history.append(ChatMessage.user(past.input))
                history.append(ChatMessage(role="assistant", content=past.output))
        return history

    def _recorder(
        self, run: _TurnRun
    ) -> Callable[[ModelCall, GenerationSpan, object, str], Awaitable[None]]:
        async def record(
            call: ModelCall, span: GenerationSpan, prompt_input: object, output: str
        ) -> None:
            event = call.to_event()
            await self._trail(run).emit(event)
            run.tally.add(call)
            run.generations.append((span, event, prompt_input, output))

        return record

    def _steps(self, run: _TurnRun, trace: TurnTrace, core_prefix: str | None) -> ModelSteps:
        return ModelSteps(
            router=self._router,
            prompts=self._prompts,
            trace=trace,
            record=self._recorder(run),
            now=run.turn.started_at,
            cache_prefix=core_prefix,
            embed_dimensions=self._embed_dimensions,
        )

    async def _run(self, run: _TurnRun) -> None:  # noqa: PLR0915
        turn, store = run.turn, run.store
        bind_log_context(turn_id=str(turn.id), workspace_id=str(run.scope.workspace_id))
        trace = self._tracer.start_turn(
            turn_id=turn.id,
            workspace_id=run.scope.workspace_id,
            user_id=run.scope.user_id,
            turn_input=turn.input,
            metadata={"config_hash": self._config_hash, "kind": turn.kind.value},
        )
        run.trace = trace
        trail = self._trail(run)
        emit = trail.emit
        queue = run.queue

        def write(text: str) -> None:
            if queue is not None:
                queue.put_nowait(TokenDelta(text))

        answer: str | None = None
        error: ErrorEvent | None = None
        log.info("turn.started")
        try:
            core = await self._memory.reader(run.scope).core()
            steps = self._steps(run, trace, core.text)
            now = TurnNow(turn.started_at, run.timezone)
            outcomes: list[IngestOutcome] = []

            async def ingest() -> IngestOutcome:
                outcome = await self._pipeline.run(
                    IngestContext(
                        scope=run.scope,
                        turn_id=turn.id,
                        message=turn.input,
                        now=now,
                        emit=emit,
                        steps=steps,
                        memory=self._memory,
                        default_lead_minutes=run.default_lead_minutes,
                        secret_found=bool(run.secret_kinds),
                        secret_kinds=run.secret_kinds,
                        trail=trail,
                    )
                )
                outcomes.append(outcome)
                return outcome

            context = TurnContext(
                router=self._router,
                prompts=self._prompts,
                trace=trace,
                now=turn.started_at.astimezone(ZoneInfo(run.timezone)),
                timezone=run.timezone,
                history=run.history,
                emit=emit,
                record_model_call=self._recorder(run),
                steps=steps,
                ingest=ingest,
                secret_found=bool(run.secret_kinds),
                core_prefix=core.text,
                trail=trail,
                write=write,
            )
            secret_values: list[str] = []
            async for chunk in self._graph.astream(
                {"message": turn.input}, context=context, stream_mode="values"
            ):
                if isinstance(chunk, dict):
                    if chunk.get("answer") is not None:
                        answer = str(chunk["answer"])
                    secret_values = list(chunk.get("secret_values") or secret_values)
            if answer is None:
                raise RuntimeError("turn graph finished without an answer")
            secret_values += steps.secrets
            run.secret_values = secret_values
            if secret_values:
                run.redacted_input = redact_values(turn.input, secret_values)
                await store.redact_input(turn.id, run.redacted_input)
            renamed = {e for o in outcomes for e in o.renamed_entities}
            if renamed and self._on_renamed is not None:
                try:
                    await self._on_renamed(run.scope, renamed)
                except Exception:
                    log.exception("turn.rerender_enqueue_failed")
        except ProviderUnavailableError as exc:
            log.warning("turn.provider_unavailable", step=exc.step, attempts=exc.detail)
            error = ErrorEvent(code=exc.code, message=exc.message, step=exc.step, retryable=True)
        except ExtractionInvalidError as exc:
            error = ErrorEvent(code=exc.code, message=exc.message, step="extract")
        except asyncio.CancelledError:
            error = ErrorEvent(code="cancelled", message=INTERNAL_ERROR_MESSAGE)
            await asyncio.shield(self._finish(run, None, error))
            raise
        except Exception:
            log.exception("turn.crashed")
            error = ErrorEvent(code="internal_error", message=INTERNAL_ERROR_MESSAGE)
        await self._finish(run, answer, error)

    # ------------------------------------------------------------------ non-chat turns

    async def undo(self, scope: WorkspaceScope, *, turn_id: uuid.UUID, timezone: str) -> Turn:
        """Revert every write ``turn_id`` made, as a new turn with its own diff (FR-10.1)."""
        undone = await self._stores(scope).get(turn_id)
        if undone is None:
            raise ValidationFailedError("that turn doesn't exist")

        async def action(
            writer_turn: WriterTurn, emit: Callable[[TurnEvent], Awaitable[None]], steps: ModelSteps
        ) -> str:
            commit = await self._memory.undo(scope, writer_turn, turn_id, emit=emit)
            await self._rebuild_keys(scope, commit, steps, timezone)
            return summarise_commit(commit, prefix="Undone")

        return await self.run_action(
            scope,
            kind=TurnKind.UNDO,
            text=f"Undo turn {turn_id}",
            timezone=timezone,
            action=action,
            parent_turn_id=turn_id,
        )

    async def confirm_held(
        self, scope: WorkspaceScope, *, held_id: uuid.UUID, timezone: str
    ) -> Turn:
        """Apply a held write as a new turn, so it has its own diff and can be undone."""
        held = await self._memory.reader(scope).held_write(held_id)
        if held is None:
            raise ValidationFailedError("that held write doesn't exist")

        async def action(
            writer_turn: WriterTurn, emit: Callable[[TurnEvent], Awaitable[None]], steps: ModelSteps
        ) -> str:
            commit = await self._memory.confirm_held(scope, writer_turn, held_id, emit=emit)
            await self._rebuild_keys(scope, commit, steps, timezone)
            return summarise_commit(commit, prefix="Confirmed")

        return await self.run_action(
            scope,
            kind=TurnKind.CONFIRM,
            text=f"Confirm: {held.title}",
            timezone=timezone,
            action=action,
            parent_turn_id=held.turn_id,
        )

    async def expire_quick(self, scope: WorkspaceScope, *, timezone: str) -> Turn | None:
        """Quick-layer housekeeping as a system turn, so it's auditable and undoable (S2.9).
        No turn is recorded when there is nothing to do."""
        ops = await self._memory.expiry_ops(scope, self._clock())
        if not ops:
            return None

        async def action(
            writer_turn: WriterTurn, emit: Callable[[TurnEvent], Awaitable[None]], steps: ModelSteps
        ) -> str:
            # Ops are re-planned at the turn's own instant; the quick flags don't feed keys.
            writer = self._memory.writer(scope, writer_turn, emit=emit)
            writer.add(*await self._memory.expiry_ops(scope, writer_turn.now))
            return summarise_commit(await writer.commit(), prefix="Quick layer tidied")

        return await self.run_action(
            scope,
            kind=TurnKind.SYSTEM,
            text="Tidy the quick layer",
            timezone=timezone,
            action=action,
        )

    async def rerender_entity_keys(
        self, scope: WorkspaceScope, *, entity_ids: Sequence[uuid.UUID], timezone: str
    ) -> Turn:
        """Re-render the keys of every item linked to renamed or relabelled entities, as a
        system turn (S2.14). Keys are derived data, so the turn has no memory diff."""

        async def action(
            writer_turn: WriterTurn, emit: Callable[[TurnEvent], Awaitable[None]], steps: ModelSteps
        ) -> str:
            reader = self._memory.reader(scope)
            items: set[uuid.UUID] = set()
            for entity_id in entity_ids:
                items.update(await reader.entity_items(entity_id))
            if not items:
                return "No memories to re-render."
            report = await self._memory.keys(
                scope, timezone=timezone, embed=self._embedder(steps), model=steps.embedding_model
            ).rebuild(sorted(items))
            noun = "memory" if report.items == 1 else "memories"
            return f"Re-rendered the search keys of {report.items} {noun}."

        return await self.run_action(
            scope,
            kind=TurnKind.SYSTEM,
            text="Re-render search keys after an entity change",
            timezone=timezone,
            action=action,
        )

    async def run_action(
        self,
        scope: WorkspaceScope,
        *,
        kind: TurnKind,
        text: str,
        timezone: str,
        action: TurnAction,
        parent_turn_id: uuid.UUID | None = None,
    ) -> Turn:
        """Run a non-chat turn to completion and return it (no streaming)."""
        store = self._stores(scope)
        turn = await store.create(
            turn_id=new_id(),
            text=text,
            config_hash=self._config_hash,
            started_at=self._clock(),
            kind=kind,
            parent_turn_id=parent_turn_id,
        )
        run = _TurnRun(
            scope=scope, store=store, turn=turn, history=[], timezone=timezone, queue=None
        )
        trace = self._tracer.start_turn(
            turn_id=turn.id,
            workspace_id=scope.workspace_id,
            user_id=scope.user_id,
            turn_input=text,
            metadata={"config_hash": self._config_hash, "kind": kind.value},
        )
        run.trace = trace
        trail = self._trail(run)
        step = {TurnKind.UNDO: AgentStep.UNDO, TurnKind.CONFIRM: AgentStep.CONFIRM}.get(kind)

        writer_turn = WriterTurn(
            turn_id=turn.id, workspace_id=scope.workspace_id, kind=kind.value, now=turn.started_at
        )
        reply: str | None = None
        error: ErrorEvent | None = None
        try:
            if step is None:
                reply = await action(writer_turn, trail.emit, self._steps(run, trace, None))
            else:
                async with trail.run(step):
                    reply = await action(writer_turn, trail.emit, self._steps(run, trace, None))
        except ValidationFailedError as exc:
            error = ErrorEvent(code=exc.code, message=exc.message)
        except Exception:
            log.exception("turn.action_crashed", kind=kind.value)
            error = ErrorEvent(code="internal_error", message=INTERNAL_ERROR_MESSAGE)
        final = await self._finish(run, reply, error)
        if error is not None and error.code == "validation_failed":
            raise ValidationFailedError(error.message)
        return final

    async def _rebuild_keys(
        self, scope: WorkspaceScope, commit: CommitResult, steps: ModelSteps, timezone: str
    ) -> None:
        if not commit.touched_items:
            return
        indexer = self._memory.keys(
            scope, timezone=timezone, embed=self._embedder(steps), model=steps.embedding_model
        )
        await indexer.rebuild(sorted(commit.touched_items))

    @staticmethod
    def _embedder(steps: ModelSteps) -> Embedder:
        """Embeddings through the ``embed`` step; keys are stored without vectors if it's down."""

        async def embed(texts: Sequence[str], hits: int) -> list[list[float]] | None:
            try:
                return await steps.embed(list(texts), hits)
            except ProviderUnavailableError:
                return None

        return embed

    # ------------------------------------------------------------------ finishing

    async def _finish(self, run: _TurnRun, answer: str | None, error: ErrorEvent | None) -> Turn:
        turn, store = run.turn, run.store
        final = turn
        try:
            if error is not None:
                with contextlib.suppress(Exception):
                    await self._trail(run).emit(error)
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
            if run.queue is not None:
                await run.queue.put(done)
            try:
                self._send_generations(run)
                if run.trace is not None:
                    run.trace.finish(
                        status=final.status.value,
                        output=final.output,
                        error=final.error_code,
                        redacted_input=run.redacted_input,
                    )
                log.info(
                    "turn.finished",
                    status=final.status.value,
                    kind=final.kind.value,
                    tokens=final.usage.total_tokens,
                    cost_usd=float(final.usage.cost_usd),
                    error_code=final.error_code,
                )
            except Exception:
                log.exception("turn.after_finish_failed")
        finally:
            if run.queue is not None:
                await run.queue.put(None)
        return final

    def _trail(self, run: _TurnRun) -> TurnTrail:
        if run.trail is None:
            run.trail = TurnTrail(run.store, run.turn.id, clock=self._clock)
        return run.trail

    @staticmethod
    def _send_generations(run: _TurnRun) -> None:
        """Finish the turn's generation spans, scrubbed of every secret the turn found."""
        secrets = run.secret_values
        for span, event, prompt_input, output in run.generations:
            if not secrets:
                span.finish(event, prompt_input=prompt_input, output=output)
                continue
            text = prompt_input if isinstance(prompt_input, str) else json.dumps(prompt_input)
            span.finish(
                event,
                prompt_input=redact_values(text, secrets),
                output=redact_values(output, secrets),
            )
        run.generations.clear()

    async def _trace_status(self) -> TraceStatus:
        if not self._tracer.enabled:
            return TraceStatus.DISABLED
        return TraceStatus.RECORDED if await self._tracer.available() else TraceStatus.UNAVAILABLE

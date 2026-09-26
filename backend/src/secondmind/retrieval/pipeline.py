"""The recall pipeline for one question (S3.4-S3.8, S3.11).

plan (model: shape + arguments; code: windows, entities, vocab) -> the shape's tools **in
parallel**, each with a timeout, next to the **soft channel** (hybrid search over every key,
mandatory filters only) -> relaxation when every filtered channel is empty -> RRF fusion
(history demoted) -> rerank -> selection -> the answer, streamed, with its ``[n]`` markers
mapped to memories and turns in code. With no evidence, a template answers and no model is
called. Reads never write memory; access bookkeeping is recorded after the answer.
"""

import asyncio
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Literal
from zoneinfo import ZoneInfo

from secondmind.config import Step
from secondmind.core import (
    AgentStep,
    AggregateTrace,
    Citation,
    CitationsEvent,
    CountCheck,
    EntityKind,
    EntityRole,
    Expansion,
    GroupValue,
    KeyKind,
    Kind,
    RelaxStep,
    RetrievalCandidate,
    RetrievalEvent,
    Shape,
    SubQueryTrace,
    TimeClock,
    TimingSpan,
    ToolCallEvent,
    ToolRun,
    Trail,
    WorkspaceScope,
    utc_now,
)
from secondmind.ingestion import ModelSteps, TurnNow, Window
from secondmind.memory import CoreView, EntityRecord, ItemRecord, Memory, format_day
from secondmind.observability import get_logger
from secondmind.providers import ChatMessage, ProviderUnavailableError
from secondmind.retrieval.answer import (
    AnswerVars,
    CitationFilter,
    Evidence,
    Part,
    abstention,
    build_pack,
    citations,
)
from secondmind.retrieval.fusion import SOFT, Candidate, fuse
from secondmind.retrieval.plan import PlanContext, Planner, ResolvedPlan, SubQuery
from secondmind.retrieval.select import (
    Selection,
    SelectSettings,
    count_check,
    rerank,
    select,
)
from secondmind.retrieval.softquery import soft_query_text
from secondmind.retrieval.tools import (
    AggregateResult,
    ConversationHit,
    Filters,
    HistoryResult,
    Hit,
    Occurrence,
    Query,
    RecallStore,
    TimelineResult,
    WindowFilter,
)

log = get_logger(__name__)

Write = Callable[[str], None]
# The stored text of a past turn: (the user's message, the reply).
Said = Callable[[uuid.UUID], Awaitable[tuple[str, str | None] | None]]

# Shapes whose empty filtered channels are loosened step by step. Not count (zero is an
# answer), history and why (the chain is the answer) or conversation.
RELAXABLE = frozenset(
    {
        Shape.EXACT, Shape.LIST, Shape.LATEST, Shape.TIME_WINDOW, Shape.ORDER, Shape.SET,
        Shape.ENTITY, Shape.SEMANTIC, Shape.SITUATIONAL,
    }
)  # fmt: skip
FILTERED_TOOLS = frozenset({"lookup", "search", "timeline", "entity"})


@dataclass(frozen=True, slots=True)
class RecallSettings:
    soft_channel_enabled: bool = True
    soft_channel_k: int = 20
    rrf_k: int = 60
    history_demotion: float = 0.5
    rerank_enabled: bool = True
    rerank_top_n: int = 20
    rerank_min_score: float = 0.5
    answer_top_k: int = 8
    list_max_items: int = 20
    tool_timeout_ms: int = 3_000
    count_check_min_score: float = 0.6

    @property
    def select(self) -> SelectSettings:
        return SelectSettings(
            rerank_min_score=self.rerank_min_score,
            answer_top_k=self.answer_top_k,
            list_max_items=self.list_max_items,
            count_check_min_score=self.count_check_min_score,
        )


@dataclass(slots=True)
class RecallContext:
    scope: WorkspaceScope
    turn_id: uuid.UUID
    message: str
    now: TurnNow
    steps: ModelSteps
    memory: Memory
    store: RecallStore
    core: CoreView
    trail: Trail
    write: Write
    history: Sequence[ChatMessage] = ()
    saved_first: bool = False
    said: Said | None = None


@dataclass(slots=True)
class RecallOutcome:
    reply: str
    event: RetrievalEvent
    citations: CitationsEvent
    message_vector: list[float] | None = None
    retrieved: list[uuid.UUID] = field(default_factory=list)
    cited: list[uuid.UUID] = field(default_factory=list)
    count_offer: CountCheck | None = None
    said_offer: list[uuid.UUID] = field(default_factory=list)
    answered_by_model: bool = False


@dataclass(slots=True)
class _Run:
    """One sub-query's retrieval: its channels and what each tool reported."""

    sub: SubQuery
    channels: dict[str, list[Hit]] = field(default_factory=dict)
    runs: list[ToolRun] = field(default_factory=list)
    aggregate: AggregateResult | None = None
    occurrences: list[Occurrence] = field(default_factory=list)
    said: list[ConversationHit] = field(default_factory=list)
    history: HistoryResult | None = None
    relaxation: list[RelaxStep] = field(default_factory=list)
    relaxed: str = ""
    soft_text: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    selection: Selection | None = None
    count_check: CountCheck | None = None

    @property
    def filtered_empty(self) -> bool:
        filtered = [c for c in self.channels if c.split(":")[0] in FILTERED_TOOLS]
        return bool(filtered) and all(not self.channels[c] for c in filtered)


@dataclass(slots=True)
class _Hydrated:
    items: dict[uuid.UUID, ItemRecord]
    entities: dict[uuid.UUID, EntityRecord]
    roles: dict[uuid.UUID, list[tuple[uuid.UUID, EntityRole]]]
    keys: dict[uuid.UUID, str]


class RecallPipeline:
    def __init__(self, settings: RecallSettings | None = None) -> None:
        self._s = settings or RecallSettings()

    async def run(self, ctx: RecallContext) -> RecallOutcome:
        timings: list[TimingSpan] = []
        async with ctx.trail.run(AgentStep.PLAN):
            plan = await Planner().plan(
                PlanContext(
                    message=ctx.message,
                    now=ctx.now,
                    steps=ctx.steps,
                    reader=ctx.memory.reader(ctx.scope),
                    store=ctx.store,
                    core=ctx.core,
                    history=ctx.history,
                    saved_first=ctx.saved_first,
                    emit=ctx.trail.emit,
                )
            )

        async with ctx.trail.run(AgentStep.SEARCH):
            runs = [_Run(sub) for sub in plan.sub_queries]
            vectors = await self._embed(ctx, runs)
            await asyncio.gather(*(self._retrieve(ctx, run, vectors) for run in runs))
            extra = await self._situational(ctx, runs)
            if extra:
                await asyncio.gather(*(self._retrieve(ctx, run, vectors) for run in extra))
                runs.extend(extra)
            for n, run in enumerate(runs, start=1):
                run.sub.index = n
            hydrated = await self._hydrate(ctx, runs)

        async with ctx.trail.run(AgentStep.RANK):
            rerank_mode, rerank_note = await self._rank(ctx, runs, hydrated, timings)
            event = RetrievalEvent(
                question=ctx.message,
                plan_source=plan.source,
                plan_note=plan.note,
                sub_queries=[],
                soft_channel=self._s.soft_channel_enabled,
                rerank=rerank_mode,
                rerank_note=rerank_note,
                timings=timings,
                explanation=_explain(plan, runs),
            )

        async with ctx.trail.run(AgentStep.ANSWER):
            outcome = await self._answer(ctx, runs, hydrated)
        event = event.model_copy(
            update={"sub_queries": [_trace(r, hydrated, outcome) for r in runs]}
        )
        await ctx.trail.emit(event)
        await ctx.trail.emit(outcome.citations)
        outcome.event = event
        outcome.message_vector = vectors.get(ctx.message)
        await ctx.memory.record_access(
            ctx.scope,
            turn_id=ctx.turn_id,
            at=ctx.now.instant,
            retrieved=outcome.retrieved,
            cited=outcome.cited,
        )
        return outcome

    # ------------------------------------------------------------------ search

    async def _embed(self, ctx: RecallContext, runs: Sequence[_Run]) -> dict[str, list[float]]:
        """One embedding call for the turn: the message and every query text."""
        texts = [ctx.message]
        for run in runs:
            run.soft_text = soft_query_text(run.sub, ctx.now.timezone)
            texts += [run.soft_text, _search_text(run.sub)]
        texts = list(dict.fromkeys(t for t in texts if t))
        try:
            vectors = await ctx.steps.embed(texts)
        except ProviderUnavailableError:
            log.warning("recall.embed_unavailable")
            return {}
        return dict(zip(texts, vectors or [], strict=False))

    async def _retrieve(
        self, ctx: RecallContext, run: _Run, vectors: Mapping[str, list[float]]
    ) -> None:
        sub = run.sub
        calls = self._calls(ctx, run, sub.filters, vectors)
        if self._s.soft_channel_enabled and run.soft_text:
            query = Query(run.soft_text, vectors.get(run.soft_text), ctx.steps.embedding_model)
            calls[SOFT] = (
                partial(
                    ctx.store.search, query, Filters(), sub.access, limit=self._s.soft_channel_k
                ),
                {"query": run.soft_text, "filters": "mandatory only"},
            )
        await self._call_all(ctx, run, calls)
        if sub.shape in RELAXABLE and run.filtered_empty:
            await self._relax(ctx, run, vectors)

    def _calls(
        self,
        ctx: RecallContext,
        run: _Run,
        filters: Filters,
        vectors: Mapping[str, list[float]],
    ) -> dict[str, tuple[Callable[[], Awaitable[Any]], dict[str, str]]]:
        """The calls the sub-query's shape maps to (``SHAPE_TOOLS``), with their arguments."""
        sub, store, access = run.sub, ctx.store, run.sub.access
        calls: dict[str, tuple[Callable[[], Awaitable[Any]], dict[str, str]]] = {}
        described = filters.describe()
        for tool in sub.tools:
            if tool == "lookup" and not _unfiltered(filters, sub):
                f = filters.without(current_only=True) if sub.shape is Shape.LATEST else filters
                if sub.shape is Shape.SITUATIONAL:
                    # Goals and intentions have no date: the window is the timeline's.
                    f = f.without(window=None)
                calls["lookup"] = (
                    partial(
                        store.lookup,
                        f,
                        access,
                        set_op=sub.set_op,
                        limit=self._s.list_max_items * 3,
                    ),
                    f.describe() | _set_args(sub),
                )
            elif tool == "aggregate" and sub.aggregate is not None:
                agg = sub.aggregate
                calls["aggregate"] = (
                    partial(
                        store.aggregate,
                        filters,
                        access,
                        op=agg.op,
                        field=agg.field,
                        group_by=agg.group_by,
                        timezone=ctx.now.timezone,
                    ),
                    described
                    | {"op": agg.op, "field": agg.field or "", "group_by": agg.group_by or ""},
                )
            elif tool == "search":
                text = _search_text(sub)
                query = Query(text, vectors.get(text), ctx.steps.embedding_model)
                calls["search"] = (
                    partial(store.search, query, filters, access, limit=self._s.soft_channel_k),
                    described | {"query": text},
                )
            elif tool == "entity":
                base, hops = _entity_path(sub)
                if base:
                    calls["entity"] = (
                        partial(store.entity, base, access, path=hops, limit=50),
                        {
                            "entities": str(len(base)),
                            "path": " → ".join(h.relation for h in hops),
                        },
                    )
            elif tool == "timeline":
                window = _timeline_window(sub, filters, ctx.now)
                if window is not None:
                    rest = filters.without(window=None)
                    calls["timeline"] = (
                        partial(
                            store.timeline,
                            window,
                            rest,
                            access,
                            now=ctx.now.instant,
                            timezone=ctx.now.timezone,
                        ),
                        rest.describe() | _window_args(window),
                    )
            elif tool == "history":
                calls["history"] = (
                    partial(self._history, ctx, run, filters),
                    described | {"shape": sub.shape.value},
                )
            elif tool == "conversation":
                query = Query(sub.question, vectors.get(sub.question), ctx.steps.embedding_model)
                said = _said_window(sub)
                calls["conversation"] = (
                    partial(
                        store.conversation,
                        query,
                        role=sub.role,
                        window=said,
                        exclude_turn=ctx.turn_id,
                        limit=10,
                    ),
                    {"query": sub.question, "role": sub.role or "any"}
                    | (_window_args(said) if said else {}),
                )
        return calls

    async def _history(self, ctx: RecallContext, run: _Run, filters: Filters) -> HistoryResult:
        sub = run.sub
        if filters.predicate:
            subject = filters.entity_ids[0] if filters.entity_ids else None
            if subject is None:
                subject = (await ctx.memory.reader(ctx.scope).self_entity()).id
            return await ctx.store.history(
                sub.access, subject_entity_id=subject, predicate=filters.predicate
            )
        # No subject + predicate: find what the question is about first, then its chain.
        text = _search_text(sub)
        seeds = await ctx.store.search(
            Query(text, None, ctx.steps.embedding_model),
            filters.without(window=None),
            sub.access,
            limit=3,
        )
        return await ctx.store.history(sub.access, item_ids=[h.item_id for h in seeds])

    async def _call_all(
        self,
        ctx: RecallContext,
        run: _Run,
        calls: Mapping[str, tuple[Callable[[], Awaitable[Any]], dict[str, str]]],
        *,
        label: str = "",
    ) -> None:
        async def one(
            name: str, factory: Callable[[], Awaitable[Any]], args: dict[str, str]
        ) -> None:
            started = utc_now()
            t0 = time.perf_counter()
            error: str | None = None
            result: Any = None
            try:
                result = await asyncio.wait_for(factory(), self._s.tool_timeout_ms / 1000)
            except TimeoutError:
                error = f"timed out after {self._s.tool_timeout_ms} ms"
            except Exception as exc:  # a failed tool never fails the turn
                log.warning("recall.tool_failed", tool=name, error=type(exc).__name__)
                error = f"{type(exc).__name__}"
            latency = round((time.perf_counter() - t0) * 1000)
            hits = self._absorb(run, name, result) if result is not None else []
            tool = name if not label else f"{name}:{label}"
            run.channels[tool] = hits
            run.runs.append(
                ToolRun(tool=tool, arguments=args, count=len(hits), latency_ms=latency, error=error)
            )
            await ctx.trail.emit(
                ToolCallEvent(
                    tool=f"recall.{tool}",
                    access="read",
                    arguments={k: v for k, v in args.items() if v} | {"part": str(run.sub.index)},
                    result_summary=error or f"{len(hits)} found",
                    count=len(hits),
                    started_at=started,
                    latency_ms=latency,
                    error=error,
                )
            )
            ctx.steps.span(
                f"recall.{tool}",
                started_at=started,
                latency_ms=latency,
                metadata={"count": str(len(hits)), "error": error or ""},
            )

        await asyncio.gather(*(one(n, f, a) for n, (f, a) in calls.items()))

    def _absorb(self, run: _Run, name: str, result: Any) -> list[Hit]:
        if isinstance(result, AggregateResult):
            run.aggregate = result
            return [Hit(item_id=i, rank=n + 1) for n, i in enumerate(result.item_ids)]
        if isinstance(result, TimelineResult):
            run.occurrences.extend(result.occurrences)
            return result.hits
        if isinstance(result, HistoryResult):
            run.history = result
            return result.hits
        if isinstance(result, list) and result and isinstance(result[0], ConversationHit):
            run.said = list(result)
            return []
        if hasattr(result, "hits"):
            return list(result.hits)
        return list(result) if isinstance(result, list) else []

    async def _relax(
        self, ctx: RecallContext, run: _Run, vectors: Mapping[str, list[float]]
    ) -> None:
        """Loosen one filter at a time and re-run the filtered tools; stop at the first step
        that finds something. Every step tried is recorded."""
        sub = run.sub
        filters = sub.filters
        original = sub.window
        for step, loosened, change in _ladder(sub, filters, ctx.now.timezone):
            filters = loosened
            calls = {
                k: v
                for k, v in self._calls(ctx, run, filters, vectors).items()
                if k.split(":")[0] in FILTERED_TOOLS
            }
            if not calls:
                continue
            await self._call_all(ctx, run, calls, label=f"relaxed:{step}")
            found = sum(len(run.channels[f"{k}:relaxed:{step}"]) for k in calls)
            run.relaxation.append(RelaxStep(step=step, change=change, count=found))
            if found:
                for k in calls:
                    run.channels[k] = run.channels.pop(f"{k}:relaxed:{step}")
                closest = ""
                if original is not None and step in ("window_month", "window_wide"):
                    closest = _closest(run, original, ctx.now.timezone)
                run.relaxed = f"{change}; found {found}{closest}"
                run.sub.filters = filters
                return
        run.sub.filters = sub.filters

    async def _situational(self, ctx: RecallContext, runs: Sequence[_Run]) -> list[_Run]:
        """S3.11: unconsumed resources linked to the topics of the intentions found for a
        situational question are always added."""
        situational = [
            r
            for r in runs
            if r.sub.shape is Shape.SITUATIONAL
            or (r.sub.expansion is not None and r.sub.expansion.source == "planner")
        ]
        if not situational:
            return []
        reader = ctx.memory.reader(ctx.scope)
        ids = sorted({h.item_id for r in situational for hits in r.channels.values() for h in hits})
        intentions = [
            i
            for i in await reader.items(ids)
            if i.kind is Kind.INTENTION and i.state in ("wanted", "active")
        ]
        if not intentions:
            return []
        rows = await reader.item_entities([i.id for i in intentions])
        entities = {e.id: e for e in await reader.entities([EntityKind.TOPIC])}
        topics = sorted({r.entity_id for r in rows if r.entity_id in entities})
        if not topics:
            return []
        names = ", ".join(entities[t].name for t in topics)
        sub = SubQuery(
            index=0,
            shape=Shape.LIST,
            question=f"What saved resources haven't I read yet on {names}?",
            topic=f"resources on {names}",
            filters=Filters(kinds=(Kind.RESOURCE,), states=("saved",), entity_ids=tuple(topics)),
            access=situational[0].sub.access,
            expansion=Expansion(
                source="code",
                reason=f"unread resources linked to the intentions' topics ({names})",
            ),
        )
        run = _Run(sub)
        run.soft_text = None  # the parent question's soft channel already ran
        return [run]

    async def _hydrate(self, ctx: RecallContext, runs: Sequence[_Run]) -> _Hydrated:
        reader = ctx.memory.reader(ctx.scope)
        ids = sorted({h.item_id for r in runs for hits in r.channels.values() for h in hits})
        items = {i.id: i for i in await reader.items(ids) if i.status.value == "active"}
        entities = {e.id: e for e in await reader.entities()}
        roles: dict[uuid.UUID, list[tuple[uuid.UUID, EntityRole]]] = {}
        for row in await reader.item_entities(list(items)):
            roles.setdefault(row.item_id, []).append((row.entity_id, row.role))
        keys: dict[uuid.UUID, str] = {}
        for key in await reader.keys(list(items)):
            if key.key_kind is KeyKind.VERBAL or (
                key.key_kind is KeyKind.TEXT and key.item_id not in keys
            ):
                keys[key.item_id] = key.text
        return _Hydrated(items=items, entities=entities, roles=roles, keys=keys)

    # ------------------------------------------------------------------ rank

    async def _rank(
        self,
        ctx: RecallContext,
        runs: Sequence[_Run],
        h: _Hydrated,
        timings: list[TimingSpan],
    ) -> tuple[Literal["model", "disabled", "failed", "skipped"], str]:
        started, t0 = utc_now(), time.perf_counter()
        for run in runs:
            run.candidates = fuse(
                run.channels,
                h.items,
                shape=run.sub.shape,
                rrf_k=self._s.rrf_k,
                history_demotion=self._s.history_demotion,
            )
        timings.append(TimingSpan(name="fusion", started_at=started, latency_ms=_ms(t0)))
        mode: Literal["model", "disabled", "failed", "skipped"] = "model"
        note = ""
        if not self._s.rerank_enabled:
            mode, note = "disabled", "RERANK_ENABLED=false: fused order with a floor"
        elif not any(r.candidates for r in runs):
            mode, note = "skipped", "nothing to rerank"
        else:
            try:
                await asyncio.gather(
                    *(
                        rerank(
                            ctx.steps,
                            run.sub.question,
                            run.candidates[: self._s.rerank_top_n],
                            h.items,
                            h.keys,
                            now=ctx.now.instant,
                            timezone=ctx.now.timezone,
                        )
                        for run in runs
                        if run.candidates
                    )
                )
            except ProviderUnavailableError as exc:
                mode, note = "failed", f"rerank unavailable ({exc.message}); fused order used"
                for run in runs:
                    for c in run.candidates:
                        c.rerank_score = None
        started, t0 = utc_now(), time.perf_counter()
        for run in runs:
            counted = run.aggregate.item_ids if run.aggregate else []
            run.selection = select(
                run.sub,
                run.candidates,
                reranked=mode == "model",
                settings=self._s.select,
                counted=counted,
            )
            if run.sub.shape is Shape.COUNT and run.aggregate is not None:
                run.count_check = count_check(
                    run.sub,
                    run.candidates,
                    h.items,
                    counted,
                    min_score=self._s.count_check_min_score,
                )
        timings.append(TimingSpan(name="selection", started_at=started, latency_ms=_ms(t0)))
        return mode, note

    # ------------------------------------------------------------------ answer

    async def _answer(
        self, ctx: RecallContext, runs: Sequence[_Run], h: _Hydrated
    ) -> RecallOutcome:
        parts, evidence = await self._parts(ctx, runs, h)
        pieces: list[str] = []
        used: list[int] = []
        stripped = 0
        by_model = False

        def say(text: str) -> None:
            if text:
                pieces.append(text)
                ctx.write(text)

        answerable = [p for p in parts if not p.empty]
        if not answerable:
            say(abstention(parts))
        else:
            pack = build_pack(parts, now=ctx.now, entities=h.entities, roles=h.roles)
            flt = CitationFilter(evidence)
            variables = AnswerVars(
                now=ctx.now.local.strftime("%A %Y-%m-%d %H:%M"),
                timezone=ctx.now.timezone,
                context=pack,
            )
            messages = [*list(ctx.history)[-6:], ChatMessage.user(ctx.message)]
            by_model = True
            async for delta in ctx.steps.stream(Step.ANSWER, variables, messages):
                say(flt.feed(delta))
            say(flt.finish())
            used, stripped = flt.used, flt.stripped
            empty = abstention([p for p in parts if p.empty and len(parts) > 1])
            if empty:
                say(f"\n\n{empty}")
        offer = next((r.count_check for r in runs if r.count_check is not None), None)
        if offer is not None:
            say(f"\n\n{offer.note} {offer.offer}")
        cited = [evidence[n] for n in used if n in evidence]
        if not by_model:
            cited = []
        items_cited = [e.item.id for e in cited if e.item is not None]
        counted = [i for r in runs if r.aggregate for i in r.aggregate.item_ids]
        items_cited = list(dict.fromkeys(items_cited + counted))
        retrieved = [e.item.id for e in evidence.values() if e.item is not None]
        return RecallOutcome(
            reply="".join(pieces),
            event=RetrievalEvent(question=ctx.message),
            citations=CitationsEvent(
                citations=[*citations(used, evidence), *_counted_citations(runs, evidence, used)],
                stripped=stripped,
                evidence=len(evidence),
            ),
            retrieved=retrieved,
            cited=items_cited,
            count_offer=offer,
            said_offer=[
                e.turn_id for e in cited if e.kind == "turn" and e.role == "assistant" and e.turn_id
            ],
            answered_by_model=by_model,
        )

    async def _parts(
        self, ctx: RecallContext, runs: Sequence[_Run], h: _Hydrated
    ) -> tuple[list[Part], dict[int, Evidence]]:
        evidence: dict[int, Evidence] = {}
        marker_of: dict[uuid.UUID, int] = {}

        def add(ev: Evidence) -> Evidence:
            target = ev.target
            if target is not None and target in marker_of:
                return evidence[marker_of[target]]
            ev.marker = len(evidence) + 1
            evidence[ev.marker] = ev
            if target is not None:
                marker_of[target] = ev.marker
            return ev

        parts: list[Part] = []
        for run in runs:
            part = Part(sub=run.sub, relaxed=run.relaxed, anchor=run.sub.anchor)
            selection = run.selection or Selection(selected=[])
            part.more = selection.more
            for cand in selection.selected:
                item = h.items.get(cand.item_id)
                if item is None:
                    continue
                part.evidence.append(
                    add(
                        Evidence(
                            marker=0,
                            kind="item",
                            title=item.title,
                            item=item,
                            soft_only=cand.soft_only,
                            counted=run.sub.shape is Shape.COUNT,
                            occurrences=[
                                o
                                for o in run.occurrences
                                if o.item_id == item.id and o.via == "routine"
                            ],
                        )
                    )
                )
            if run.aggregate is not None and run.sub.shape is Shape.COUNT:
                part.count = run.aggregate.value
                if run.count_check is not None:
                    part.count_note = run.count_check.note
                    for item_id in run.count_check.extra_ids:
                        item = h.items.get(item_id)
                        if item is not None:
                            part.evidence.append(
                                add(
                                    Evidence(
                                        marker=0,
                                        kind="item",
                                        title=item.title,
                                        item=item,
                                        look_alike=True,
                                    )
                                )
                            )
            for hit in run.said[:3]:
                full = await ctx.said(hit.turn_id) if ctx.said is not None else None
                text = hit.text
                if full is not None:
                    text = (full[1] if hit.role == "assistant" else full[0]) or hit.text
                part.evidence.append(
                    add(
                        Evidence(
                            marker=0,
                            kind="turn",
                            title=_said_title(hit, ctx.now.timezone),
                            turn_id=hit.turn_id,
                            role=hit.role,
                            said=" ".join(text.split())[:900],
                            said_at=hit.said_at,
                        )
                    )
                )
            parts.append(part)
        return parts, evidence


# ------------------------------------------------------------------ helpers


def _ms(t0: float) -> int:
    return max(0, round((time.perf_counter() - t0) * 1000))


def _search_text(sub: SubQuery) -> str:
    return f"{sub.question} {sub.about}" if sub.about else sub.question


def _unfiltered(filters: Filters, sub: SubQuery) -> bool:
    """A lookup with no structured filter would return everything: don't run it."""
    return filters.without(current_only=False, roles=()).empty and sub.set_op is None


def _set_args(sub: SubQuery) -> dict[str, str]:
    if sub.set_op is None:
        return {}
    out: dict[str, str] = {"set": sub.set_op.op}
    if sub.set_op.other_kind:
        out["other_kind"] = sub.set_op.other_kind.value
    return out


def _entity_path(sub: SubQuery) -> tuple[list[uuid.UUID], list[Any]]:
    for entity in sub.entities:
        if entity.hops and entity.base_ids:
            return list(entity.base_ids), list(entity.hops)
    base = [i for e in sub.entities if e.outcome == "matched" for i in e.entity_ids]
    return base, []


def _timeline_window(sub: SubQuery, filters: Filters, now: TurnNow) -> WindowFilter | None:
    if filters.window is not None:
        return filters.window
    if sub.shape is Shape.ORDER:
        if _looks_ahead(sub):
            return WindowFilter(clock=TimeClock.OCCURRED, start=now.instant, end=None)
        return WindowFilter(clock=TimeClock.OCCURRED, start=None, end=now.instant)
    if sub.shape is Shape.TIME_WINDOW:
        return WindowFilter(
            clock=TimeClock.OCCURRED, start=now.instant, end=now.instant + timedelta(days=30)
        )
    return None


def _looks_ahead(sub: SubQuery) -> bool:
    if sub.direction != "any":
        return sub.direction == "future"
    return bool(re.search(r"\b(next|upcoming|coming|until|will|soon)\b", sub.question.lower()))


def _said_window(sub: SubQuery) -> WindowFilter | None:
    window = sub.window
    if window is None or (window.start is None and window.end is None):
        return None
    return WindowFilter(clock=TimeClock.MENTIONED, start=window.start, end=window.end)


def _window_args(w: WindowFilter) -> dict[str, str]:
    start = w.start.isoformat(timespec="minutes") if w.start else "…"
    end = w.end.isoformat(timespec="minutes") if w.end else "…"
    return {"window": f"{w.clock.value} [{start}, {end})"}


def _ladder(
    sub: SubQuery, filters: Filters, tz: str
) -> list[
    tuple[
        Literal["category", "subtype", "state", "window_month", "window_wide", "entity"],
        Filters,
        str,
    ]
]:
    """The relaxation steps that change something, in order (S3.6)."""
    steps: list[tuple[Any, Filters, str]] = []
    f = filters
    if f.category:
        f = f.without(category=None)
        steps.append(("category", f, f"dropped the category {filters.category}"))
    if f.subtypes:
        f = f.without(subtypes=())
        steps.append(("subtype", f, f"dropped the subtype {', '.join(filters.subtypes)}"))
    if f.states:
        f = f.without(states=())
        steps.append(("state", f, f"dropped the state {', '.join(filters.states)}"))
    window = sub.window
    if f.window is not None and window is not None:
        month = _month_window(f.window, tz, pad=0)
        f = f.without(window=month)
        steps.append(
            (
                "window_month",
                f,
                f"nothing {_window_label(window, tz)}; widened to {_label(month, tz)}",
            )
        )
        wide = _month_window(f.window, tz, pad=1) if f.window else None
        if wide is not None:
            f = f.without(window=wide)
            steps.append(("window_wide", f, f"widened again to {_label(wide, tz)}"))
    if f.entity_ids and not sub.missing_entity:
        f = f.without(entity_ids=(), roles=())
        steps.append(("entity", f, "dropped the person or thing"))
    return steps


def _month_window(w: WindowFilter, tz: str, *, pad: int) -> WindowFilter:
    zone = ZoneInfo(tz)

    def first_of(d: datetime, shift: int) -> datetime:
        local = d.astimezone(zone)
        year, month = local.year, local.month + shift
        while month < 1:
            year, month = year - 1, month + 12
        while month > 12:
            year, month = year + 1, month - 12
        return datetime(year, month, 1, tzinfo=zone)

    start = first_of(w.start, -pad) if w.start else None
    end = first_of(w.end - timedelta(microseconds=1), 1 + pad) if w.end else None
    return WindowFilter(clock=w.clock, start=start, end=end)


def _window_label(window: Window, tz: str) -> str:
    span = _label(WindowFilter(window.clock, window.start, window.end), tz)
    return f"'{window.expression}' ({span})"


def _said_title(hit: ConversationHit, tz: str) -> str:
    who = "I" if hit.role == "assistant" else "You"
    return f"{who} said, {format_day(hit.said_at, tz)}"


def _closest(run: _Run, original: Window, tz: str) -> str:
    """ "; the closest is Wednesday 9 September 2026" (the found date nearest the window)."""
    dates = [o.start for o in run.occurrences]
    if original.start is None or not dates:
        return ""
    nearest = min(dates, key=lambda d: abs((d - original.start).total_seconds()))  # type: ignore[operator]
    return f"; the closest is {format_day(nearest, tz)}"


def _label(w: WindowFilter, tz: str) -> str:
    start = format_day(w.start, tz, weekday=False) if w.start else "the start"
    end = format_day(w.end - timedelta(microseconds=1), tz, weekday=False) if w.end else "now"
    return f"{start} to {end}"


def _explain(plan: ResolvedPlan, runs: Sequence[_Run]) -> str:
    shapes = ", ".join(r.sub.shape.value for r in runs)
    found = sum(len(r.candidates) for r in runs)
    kept = sum(len(r.selection.selected) for r in runs if r.selection)
    parts = f"{len(runs)} part{'s' if len(runs) != 1 else ''}"
    return f"{parts} ({shapes}); {found} candidates, {kept} kept; plan: {plan.source}"


def _counted_citations(
    runs: Sequence[_Run], evidence: Mapping[int, Evidence], used: Sequence[int]
) -> list[Citation]:
    """Counted items are cited whether or not the reply marked them (the count covers them)."""
    return [ev.citation() for ev in evidence.values() if ev.counted and ev.marker not in used]


def _trace(run: _Run, h: _Hydrated, outcome: RecallOutcome) -> SubQueryTrace:
    sub = run.sub
    cited = set(outcome.cited)
    for cand in run.candidates:
        cand.cited = cand.item_id in cited
    candidates = [cand.trace(h.items.get(cand.item_id)) for cand in run.candidates]
    candidates.extend(
        RetrievalCandidate(
            turn_id=hit.turn_id,
            title=f"{'I' if hit.role == 'assistant' else 'You'} said: {hit.text[:80]}",
            found_by=[],
            lexical_score=hit.lexical,
            dense_score=hit.dense,
            selected=True,
            cited=hit.turn_id in outcome.said_offer,
            reason="conversation: what was said, not saved",
        )
        for hit in run.said
    )
    agg = None
    if run.aggregate is not None and sub.aggregate is not None:
        agg = AggregateTrace(
            op=run.aggregate.op,
            field=sub.aggregate.field,
            group_by=sub.aggregate.group_by,
            value=run.aggregate.value,
            groups=[
                GroupValue(key=g.key, value=g.value, count=g.count) for g in run.aggregate.groups
            ],
            counted_ids=run.aggregate.item_ids,
        )
    return SubQueryTrace(
        index=sub.index,
        shape=sub.shape,
        question=sub.question,
        topic=sub.topic,
        filters=sub.filters.describe() | _set_args(sub),
        dropped=sub.dropped,
        windows=sub.times,
        entities=[e.trace() for e in sub.entities],
        tools=run.runs,
        soft_query=run.soft_text,
        relaxation=run.relaxation,
        expansion=sub.expansion,
        aggregate=agg,
        count_check=run.count_check,
        candidates=candidates,
        abstained=not (run.selection and run.selection.selected) and not run.said and agg is None,
    )

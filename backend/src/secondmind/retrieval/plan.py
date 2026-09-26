"""The query planner (S3.4, ADR-0025).

The model decides what kind of question it is and what it is about (``plan@1``, structured
output). Code does the rest:

* time expressions become windows through S2's resolver (``resolve_window``); an anchor ("the
  weekend before **Goa**") is looked up first and the window computed from its date;
* entities are resolved with S2's deterministic matcher, and relation paths are followed;
* kinds, subtypes, categories and predicates are normalised against the vocab, and anything
  unknown is **dropped** rather than guessed (the soft channel still covers it);
* the tools each shape runs come from ``SHAPE_TOOLS``, never from the model.

Invalid planner output is retried once with the errors; still invalid (or the planner is
down), the plan falls back to one ``semantic`` sub-query, so the soft channel still answers.
"""

import json
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from secondmind.config import Step
from secondmind.core import (
    KIND_STATES,
    EntityKind,
    EntityRole,
    EntityTrace,
    Expansion,
    Kind,
    Shape,
    TimeClock,
    TimePrecision,
    TimeResolution,
    ToolCallEvent,
    TurnEvent,
    utc_now,
)
from secondmind.ingestion import (
    ModelSteps,
    Term,
    TurnNow,
    UnresolvableTimeError,
    Window,
    match_entities,
    match_key,
    normalise,
    resolve_window,
    slugify,
    terms_for,
)
from secondmind.memory import (
    CoreView,
    EntityRecord,
    MemoryReader,
    RelationRecord,
    occurrence_length,
)
from secondmind.providers import ChatMessage, ProviderUnavailableError
from secondmind.retrieval.schemas import (
    PlanAggregate,
    PlanEntity,
    PlanFilters,
    PlanTime,
    QueryPlanOut,
    SubQueryOut,
    validate_plan,
)
from secondmind.retrieval.tools import (
    INVERSE,
    SYMMETRIC,
    Access,
    Filters,
    PathHop,
    Query,
    RecallStore,
    SetOp,
    ToolName,
    WindowFilter,
)

# Code, not the model, maps a shape to its tools (the soft channel runs for every shape).
SHAPE_TOOLS: Mapping[Shape, tuple[ToolName, ...]] = {
    Shape.EXACT: ("lookup",),
    Shape.LIST: ("lookup",),
    Shape.LATEST: ("lookup", "history"),
    Shape.HISTORY: ("history",),
    Shape.TIME_WINDOW: ("timeline", "search"),
    Shape.ORDER: ("timeline",),
    Shape.COUNT: ("aggregate",),
    Shape.SET: ("lookup",),
    Shape.ENTITY: ("entity", "lookup"),
    Shape.SEMANTIC: ("search",),
    Shape.WHY: ("history",),
    Shape.SITUATIONAL: ("lookup", "timeline"),
    Shape.CONVERSATION: ("conversation",),
}

# Words that don't make a time question about anything ("what's coming up this week?").
_FILLER = frozenset(
    {
        "a", "an", "any", "anything", "everything", "what", "whats", "is", "are", "was",
        "were", "coming", "up", "happening", "planned", "plans", "on", "for", "in", "the",
        "this", "that", "next", "last", "my", "i", "me", "do", "did", "have", "has",
    }
)  # fmt: skip
_HISTORY_TURNS = 6


class PlanVars(BaseModel):
    now: str
    timezone: str
    vocabulary: str
    entities: str
    saved_first: str


@dataclass(frozen=True, slots=True)
class ResolvedEntity:
    mention: str
    outcome: Literal["matched", "unknown", "no_relation"]
    entity_ids: tuple[uuid.UUID, ...]
    names: tuple[str, ...]
    base_ids: tuple[uuid.UUID, ...] = ()
    hops: tuple[PathHop, ...] = ()
    path: tuple[str, ...] = ()

    def trace(self) -> EntityTrace:
        return EntityTrace(
            mention=self.mention,
            outcome=self.outcome,
            path=list(self.path),
            entity_ids=list(self.entity_ids),
            names=list(self.names),
        )


@dataclass(slots=True)
class SubQuery:
    """One resolved part of the plan: everything the tools need, and its glass-box trace."""

    index: int
    shape: Shape
    question: str
    topic: str
    about: str | None = None
    filters: Filters = field(default_factory=Filters)
    windows: list[Window] = field(default_factory=list)
    times: list[TimeResolution] = field(default_factory=list)
    entities: list[ResolvedEntity] = field(default_factory=list)
    aggregate: PlanAggregate | None = None
    set_op: SetOp | None = None
    role: Literal["user", "assistant"] | None = None
    access: Access = field(default_factory=Access)
    dropped: list[str] = field(default_factory=list)
    expansion: Expansion | None = None
    # An entity the question needs doesn't exist ("Nisha's husband" after a correction): the
    # tools that filter on it are skipped, and relaxation never drops it.
    missing_entity: bool = False
    # The event a window was computed from, and when it started ("Goa trip with Kabir").
    anchor: tuple[str, datetime] | None = None
    direction: Literal["past", "future", "any"] = "any"

    @property
    def window(self) -> Window | None:
        return self.windows[0] if self.windows else None

    @property
    def tools(self) -> tuple[ToolName, ...]:
        tools = SHAPE_TOOLS[self.shape]
        if self.shape is Shape.TIME_WINDOW and not self.has_topic:
            tools = tuple(t for t in tools if t != "search")
        if self.missing_entity:
            tools = tuple(t for t in tools if t not in ("entity", "lookup", "aggregate"))
        return tools

    @property
    def has_topic(self) -> bool:
        """Is a time question about something besides its time? Then search runs too."""
        if self.about:
            return True
        words = set(re.findall(r"[a-z0-9]+", self.topic.lower()))
        for w in self.windows:
            words -= set(re.findall(r"[a-z0-9]+", w.expression.lower()))
        return bool(words - _FILLER)


@dataclass(slots=True)
class ResolvedPlan:
    question: str
    source: Literal["model", "retry", "fallback"]
    note: str
    sub_queries: list[SubQuery]


Emit = Callable[[TurnEvent], Awaitable[None]]


@dataclass(slots=True)
class PlanContext:
    message: str
    now: TurnNow
    steps: ModelSteps
    reader: MemoryReader
    store: RecallStore
    core: CoreView
    history: Sequence[ChatMessage] = ()
    saved_first: bool = False
    emit: Emit | None = None


class Planner:
    async def plan(self, ctx: PlanContext) -> ResolvedPlan:
        entities = await ctx.reader.entities()
        vocab = await ctx.reader.vocab()
        categories = await ctx.reader.categories()
        out, source, note = await self._ask(ctx, entities, vocab)
        resolver = _Resolver(ctx, entities, vocab, [c.slug for c in categories])
        subs: list[SubQuery] = []
        for n, sq in enumerate(out.sub_queries, start=1):
            resolved = await resolver.resolve(n, sq)
            if resolved is None:
                note = _join(note, f"dropped an expansion not from core: {sq.expanded_from!r}")
                continue
            subs.append(resolved)
        subs.extend(_booked_in_window(subs))
        for n, sub in enumerate(subs, start=1):
            sub.index = n
        return ResolvedPlan(question=ctx.message, source=source, note=note, sub_queries=subs)

    async def _ask(
        self, ctx: PlanContext, entities: Sequence[EntityRecord], vocab: Sequence[object]
    ) -> tuple[QueryPlanOut, Literal["model", "retry", "fallback"], str]:
        history = list(ctx.history)[-_HISTORY_TURNS:]
        said = [ctx.message, *(m.content for m in history)]
        variables = PlanVars(
            now=ctx.now.local.strftime("%A %Y-%m-%d %H:%M"),
            timezone=ctx.now.timezone,
            vocabulary=_vocab_text(vocab),
            entities=_entities_text(entities),
            saved_first=(
                "The message also saved something; that is already stored, so plan only its "
                "question."
                if ctx.saved_first
                else ""
            ),
        )
        messages = [*history, ChatMessage.user(ctx.message)]
        try:
            out = await ctx.steps.structured(Step.PLAN, QueryPlanOut, variables, messages)
        except ProviderUnavailableError as exc:
            return fallback_plan(ctx.message), "fallback", f"planner unavailable: {exc.message}"
        errors = validate_plan(out, said)
        if not errors:
            return out, "model", ""
        retry = [
            *messages,
            ChatMessage(role="assistant", content=out.model_dump_json()),
            ChatMessage.user(
                "That plan has these problems. Return the whole plan again, corrected:\n- "
                + "\n- ".join(errors)
            ),
        ]
        try:
            again = await ctx.steps.structured(Step.PLAN, QueryPlanOut, variables, retry)
        except ProviderUnavailableError as exc:
            return fallback_plan(ctx.message), "fallback", f"planner unavailable: {exc.message}"
        still = validate_plan(again, said)
        if not still:
            return again, "retry", "first plan was invalid: " + "; ".join(errors[:3])
        return (
            fallback_plan(ctx.message),
            "fallback",
            "the plan was invalid twice (" + "; ".join(still[:3]) + "); searched by meaning",
        )


def fallback_plan(message: str) -> QueryPlanOut:
    """One ``semantic`` sub-query over the whole message: the soft channel still answers."""
    topic = " ".join(message.split())
    return QueryPlanOut.model_validate(
        {
            "sub_queries": [
                {
                    "shape": "semantic",
                    "question": topic,
                    "topic": topic if len(topic) <= 80 else topic[:79] + "…",
                    "about": None,
                    "times": [],
                    "entities": [],
                    "filters": {
                        "kinds": [],
                        "subtypes": [],
                        "states": [],
                        "category": None,
                        "predicate": None,
                        "attribute": None,
                        "role": None,
                        "current_only": False,
                    },
                    "aggregate": None,
                    "set": None,
                    "role": None,
                    "about_sensitive": False,
                    "expanded_from": None,
                }
            ]
        }
    )


class _Resolver:
    def __init__(
        self,
        ctx: PlanContext,
        entities: Sequence[EntityRecord],
        vocab: Sequence[object],
        categories: Sequence[str],
    ) -> None:
        self._ctx = ctx
        self._entities = list(entities)
        self._me = next((e for e in entities if e.kind is EntityKind.SELF), None)
        by_kind: dict[str, list[tuple[str, Sequence[str]]]] = {}
        for term in vocab:
            by_kind.setdefault(term.vocab.value, []).append((term.slug, term.aliases))  # type: ignore[attr-defined]
        self._terms = {
            v: terms_for(v, by_kind.get(v, [])) for v in ("subtype", "predicate", "relation")
        }
        self._categories = [Term(c) for c in categories]
        self._relations: dict[uuid.UUID, list[RelationRecord]] = {}

    async def resolve(self, index: int, sq: SubQueryOut) -> SubQuery | None:
        sub = SubQuery(
            index=index,
            shape=Shape(sq.shape),
            question=sq.question.strip(),
            topic=sq.topic.strip(),
            about=(sq.about or "").strip() or None,
            role=sq.role,
            access=Access(sensitive=sq.about_sensitive),
            aggregate=sq.aggregate,
        )
        if sq.expanded_from:
            if not self._in_core(sq.expanded_from):
                return None
            sub.expansion = Expansion(
                source="planner",
                core_entry=sq.expanded_from,
                reason=f"from core memory: {sq.expanded_from}",
            )
        entity_ids: list[uuid.UUID] = []
        for mention in sq.entities:
            resolved = await self._entity(mention)
            sub.entities.append(resolved)
            if resolved.outcome == "matched":
                entity_ids.extend(resolved.entity_ids)
            elif resolved.hops or resolved.base_ids:
                sub.missing_entity = True
            else:
                sub.dropped.append(f"entity {mention.mention!r} (not known)")
        if sq.set is not None:
            other = Kind(sq.set.other_kind) if sq.set.other_kind else None
            partner = None
            if sq.set.entity:
                found = match_entities(sq.set.entity, self._entities, self._me)
                partner = found[0].id if found else None
            if sq.set.op == "shared_with" and partner is None:
                sub.dropped.append(f"set: {sq.set.entity!r} (not known)")
            else:
                sub.set_op = SetOp(op=sq.set.op, other_kind=other, entity_id=partner)
        filters = await self._filters(sq.filters, sub)
        for t in sq.times:
            await self._time(t, sub)
        window = sub.window
        sub.filters = filters.without(
            entity_ids=tuple(dict.fromkeys(entity_ids)),
            window=WindowFilter(clock=window.clock, start=window.start, end=window.end)
            if window is not None and (window.start is not None or window.end is not None)
            else None,
        )
        return sub

    # ------------------------------------------------------------------ entities

    async def _entity(self, p: PlanEntity) -> ResolvedEntity:
        base = match_entities(p.name or p.mention, self._entities, self._me)
        if not base and p.name:
            base = match_entities(p.mention, self._entities, self._me)
        if not base:
            return ResolvedEntity(mention=p.mention, outcome="unknown", entity_ids=(), names=())
        hops = tuple([PathHop(await self._relation(r)) for r in p.path])
        if not hops:
            return ResolvedEntity(
                mention=p.mention,
                outcome="matched",
                entity_ids=tuple(e.id for e in base),
                names=tuple(e.display for e in base),
                base_ids=tuple(e.id for e in base),
            )
        current = list(base)
        path = [base[0].display]
        for hop in hops:
            reached: list[EntityRecord] = []
            for entity in current:
                reached.extend(await self._follow(entity, hop.relation))
            current = list({e.id: e for e in reached}.values())
            path += [hop.relation, current[0].name if current else "?"]
            if not current:
                break
        return ResolvedEntity(
            mention=p.mention,
            outcome="matched" if current else "no_relation",
            entity_ids=tuple(e.id for e in current),
            names=tuple(e.display for e in current),
            base_ids=tuple(e.id for e in base),
            hops=hops,
            path=tuple(path),
        )

    async def _relation(self, proposed: str) -> str:
        slug, _ = await normalise(proposed, self._terms["relation"], vocab="relation")
        return slug

    async def _follow(self, entity: EntityRecord, relation: str) -> list[EntityRecord]:
        """Entities that are ``relation`` of ``entity`` (current relations only)."""
        if entity.id not in self._relations:
            self._relations[entity.id] = await self._ctx.reader.relations([entity.id])
        by_id = {e.id: e for e in self._entities}
        out: list[EntityRecord] = []
        backward = ({relation} & SYMMETRIC) | (
            {INVERSE[relation]} if relation in INVERSE else set()
        )
        for r in self._relations[entity.id]:
            if r.valid_to is not None:
                continue
            if r.dst_entity_id == entity.id and r.relation == relation:
                target = by_id.get(r.src_entity_id)
            elif r.src_entity_id == entity.id and r.relation in backward:
                target = by_id.get(r.dst_entity_id)
            else:
                continue
            if target is not None and target.status == "active":
                out.append(target)
        return out

    # ------------------------------------------------------------------ filters

    async def _filters(self, f: PlanFilters, sub: SubQuery) -> Filters:
        kinds = tuple(dict.fromkeys(Kind(k) for k in f.kinds))
        subtypes: list[str] = []
        for proposed in f.subtypes:
            slug, how = await normalise(proposed, self._terms["subtype"], vocab="subtype")
            if how.reused:
                subtypes.append(slug)
            else:
                sub.dropped.append(f"subtype {proposed!r} (not in the vocab)")
        allowed = {s for k in (kinds or tuple(Kind)) for s in KIND_STATES[k]}
        states = [s for s in f.states if s in allowed]
        sub.dropped.extend(
            f"state {s!r} (not a state of {kinds or 'any kind'})"
            for s in f.states
            if s not in allowed
        )
        category = None
        if f.category:
            slug, how = await normalise(f.category, self._categories, vocab="category", path=True)
            if how.reused or any(c.slug.startswith(slug + "/") for c in self._categories):
                category = slug
            else:
                sub.dropped.append(f"category {f.category!r} (not in the vocab)")
        predicate = None
        if f.predicate:
            slug, how = await normalise(f.predicate, self._terms["predicate"], vocab="predicate")
            if how.reused:
                predicate = slug
            else:
                sub.dropped.append(f"predicate {f.predicate!r} (not in the vocab)")
        attribute = (f.attribute.key.strip(), f.attribute.value.strip()) if f.attribute else None
        return Filters(
            kinds=kinds,
            subtypes=tuple(subtypes),
            states=tuple(states),
            category=category,
            roles=(EntityRole(f.role),) if f.role else (),
            predicate=predicate,
            attribute=attribute if attribute and all(attribute) else None,
            current_only=f.current_only,
        )

    # ------------------------------------------------------------------ time

    async def _time(self, t: PlanTime, sub: SubQuery) -> None:
        if sub.direction == "any":
            sub.direction = t.direction
        clock = TimeClock(t.clock)
        now = self._ctx.now
        anchor: tuple[datetime, datetime | None] | None = None
        label = None
        if t.anchor:
            found = await self._anchor(t.anchor, sub.access)
            if found is None:
                sub.dropped.append(f"time {t.expression!r} {t.anchor!r} (no such event found)")
                return
            anchor, label = found
            sub.anchor = (label, anchor[0])
        try:
            window = resolve_window(
                t.expression or "during",
                clock,
                now,
                direction=t.direction,
                anchor=anchor,
                anchor_label=label,
            )
        except UnresolvableTimeError:
            sub.dropped.append(f"time {t.expression!r} (no rule understood it)")
            return
        if window.start is None and window.end is None:
            return  # "ever": no time filter at all
        sub.windows.append(window)
        sub.times.append(time_trace(window, now))

    async def _anchor(
        self, text: str, access: Access
    ) -> tuple[tuple[datetime, datetime | None], str] | None:
        """The event a window is relative to: looked up by its words among dated items."""
        started = utc_now()
        hits = await self._ctx.store.search(
            Query(text=text, vector=None, model=""),
            Filters(kinds=(Kind.PLAN, Kind.EPISODE)),
            access,
            limit=5,
        )
        items = {i.id: i for i in await self._ctx.reader.items([h.item_id for h in hits])}
        chosen = None
        for hit in hits:
            item = items.get(hit.item_id)
            if item is not None and item.occurred_start is not None and not item.rrule:
                chosen = item
                break
        if self._ctx.emit is not None:
            await self._ctx.emit(
                ToolCallEvent(
                    tool="recall.anchor",
                    access="read",
                    arguments={"anchor": text},
                    result_summary=chosen.title if chosen else "no dated event found",
                    count=1 if chosen else 0,
                    started_at=started,
                    latency_ms=max(0, int((utc_now() - started).total_seconds() * 1000)),
                )
            )
        if chosen is None or chosen.occurred_start is None:
            return None
        end = chosen.occurred_end or chosen.occurred_start + occurrence_length(
            chosen.time_precision
        )
        return (chosen.occurred_start, end), chosen.title

    def _in_core(self, entry: str) -> bool:
        wanted = match_key(entry)
        for line in self._ctx.core.text.splitlines():
            key = match_key(line)
            if key and (wanted in key or key in wanted):
                return True
        return False


def _booked_in_window(subs: Sequence[SubQuery]) -> list[SubQuery]:
    """S3.11: a situational question with a window always sees what's already booked in it."""
    out: list[SubQuery] = []
    for sub in subs:
        window = sub.window
        if sub.shape is not Shape.SITUATIONAL or window is None or sub.filters.window is None:
            continue
        out.append(
            SubQuery(
                index=0,
                shape=Shape.TIME_WINDOW,
                question=f"What is already planned {window.expression}?",
                topic=f"plans {window.expression}",
                filters=Filters(kinds=(Kind.PLAN,), window=sub.filters.window),
                windows=[window],
                times=list(sub.times),
                access=sub.access,
                expansion=Expansion(
                    source="code",
                    reason=f"plans and routines already booked {window.expression}",
                ),
            )
        )
    return out


def time_trace(window: Window, now: TurnNow) -> TimeResolution:
    """The glass box's *expression -> window (now, tz, rule)*."""

    def show(d: datetime | None) -> str:
        if d is None:
            return "…"
        local = d.astimezone(now.local.tzinfo)
        if window.precision is TimePrecision.DATETIME or local.hour or local.minute:
            return local.strftime("%Y-%m-%dT%H:%M")
        return local.date().isoformat()

    return TimeResolution(
        expression=window.expression,
        clock=window.clock,
        value=show(window.start),
        end=show(window.end),
        precision=window.precision,
        now=now.instant,
        timezone=now.timezone,
        rule=window.rule,
        anchor=window.anchor,
    )


def _vocab_text(vocab: Sequence[object]) -> str:
    by_kind: dict[str, list[str]] = {}
    for term in vocab:
        by_kind.setdefault(term.vocab.value, []).append(term.slug)  # type: ignore[attr-defined]
    lines = [f"- {k}: {', '.join(sorted(v))}" for k, v in sorted(by_kind.items())]
    return "\n".join(lines) or "- (nothing yet)"


def _entities_text(entities: Sequence[EntityRecord]) -> str:
    lines = [
        f"- {e.display} [{e.kind.value}]"
        + (f" aliases: {', '.join(e.aliases)}" if e.aliases else "")
        for e in entities
        if e.kind is not EntityKind.SELF and e.status == "active"
    ]
    return "\n".join(lines[:200]) or "- (none yet)"


def _join(a: str, b: str) -> str:
    return f"{a}; {b}" if a else b


def plan_json(plan: QueryPlanOut) -> str:
    return json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)


__all__ = [
    "SHAPE_TOOLS",
    "PlanContext",
    "PlanVars",
    "Planner",
    "ResolvedEntity",
    "ResolvedPlan",
    "SubQuery",
    "fallback_plan",
    "plan_json",
    "slugify",
    "time_trace",
]

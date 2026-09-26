"""The ingestion pipeline for one save (S2.4-S2.10).

extract (one model call) -> resolve every time expression in code -> normalise slugs -> resolve
entities -> reconcile each memory with what is stored -> ops to the memory writer (policy,
write log, one transaction) -> enrich + rebuild keys after the commit -> an acknowledgement
built from the committed diff. The model proposes; code decides and writes.
"""

import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from pydantic import BaseModel

from secondmind.config import Step
from secondmind.core import (
    AgentStep,
    Classification,
    DecisionEvent,
    EntityKind,
    EntityResolution,
    EntityRole,
    KeyKind,
    Kind,
    Layer,
    LinkType,
    Modality,
    Normalisation,
    NullTrail,
    PolicyDecision,
    ReconcileDecision,
    Reconciliation,
    ResourceFormat,
    Sensitivity,
    StepRun,
    StepStatus,
    TimeClock,
    TimePrecision,
    TimeResolution,
    Trail,
    TriggerOn,
    TurnEvent,
    VocabKind,
    WorkspaceScope,
    initial_state,
    new_id,
    utc_now,
)
from secondmind.ingestion.ack import AckFacts, acknowledge, refusal
from secondmind.ingestion.entities import EntityPlan, resolve_entities
from secondmind.ingestion.normalise import Term, normalise, terms_for
from secondmind.ingestion.reconcile import Decision, Draft, Reconciler
from secondmind.ingestion.schemas import (
    EnrichOutput,
    EntityChoice,
    ExtractedEntity,
    ExtractOutput,
    ProposedMemory,
    ReconcileChoice,
    validate_extraction,
)
from secondmind.ingestion.steps import ModelSteps
from secondmind.ingestion.time import (
    Resolved,
    TurnNow,
    UnresolvableTimeError,
    reminder_time,
    resolve,
    resolve_lead,
    validate_rrule,
)
from secondmind.memory import (
    RELATIVE_WORDS,
    CommitResult,
    CreateItem,
    EntityLink,
    EntityRecord,
    FulfilIntention,
    ItemContent,
    ItemRecord,
    LinkItems,
    Memory,
    MemoryWriter,
    NewTrigger,
    Op,
    RelateEntities,
    SupersedeItem,
    TriggerContent,
    UpdateItem,
    WriterTurn,
    content_hash,
    format_when,
    has_relative_time,
    quick_layer,
    rrule_words,
)
from secondmind.observability import get_logger
from secondmind.providers import ChatMessage, ProviderUnavailableError

log = get_logger(__name__)

Emit = Callable[[TurnEvent], Awaitable[None]]
_PLACEHOLDER = re.compile(r"\[\[(t\d+)\]\]")
_SECRET_TITLE = "a secret (not shown)"  # noqa: S105 - a display label


class ExtractionInvalidError(Exception):
    """The model's extraction was still invalid after one retry with the errors."""

    code = "extraction_invalid"
    message = "I couldn't make sense of that well enough to save it, so nothing was written."


@dataclass(frozen=True, slots=True)
class IngestSettings:
    reconcile_threshold: float = 0.85
    cue_keys_max: int = 3
    enrich_enabled: bool = True
    quick_horizon_days: int = 30
    quick_recent_days: int = 7


@dataclass(slots=True)
class IngestContext:
    scope: WorkspaceScope
    turn_id: uuid.UUID
    message: str
    now: TurnNow
    emit: Emit
    steps: ModelSteps
    memory: Memory
    default_lead_minutes: int = 1440
    secret_found: bool = False
    secret_kinds: Sequence[str] = ()
    trail: Trail = field(default_factory=NullTrail)


@dataclass(slots=True)
class IngestOutcome:
    reply: str
    commit: CommitResult | None = None
    decision: DecisionEvent | None = None
    secret_values: list[str] = field(default_factory=list)
    renamed_entities: set[uuid.UUID] = field(default_factory=set)


class ExtractVars(BaseModel):
    now: str
    timezone: str
    entities: str
    categories: str
    vocabulary: str


class EnrichVars(BaseModel):
    cues_max: int


class ResolveVars(BaseModel):
    mention: str
    candidates: str


class ReconcileVars(BaseModel):
    proposed: str
    candidates: str


@dataclass(slots=True)
class _Plan:
    """A proposed memory on its way to becoming ops."""

    proposed: ProposedMemory
    draft: Draft
    title: str
    category_slug: str | None
    triggers: list[NewTrigger]
    core: bool
    decision: Decision | None = None


@dataclass(slots=True)
class _Notes:
    times: list[TimeResolution] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    normalisations: list[Normalisation] = field(default_factory=list)
    models: dict[str, str] = field(default_factory=dict)


class IngestionPipeline:
    def __init__(self, settings: IngestSettings | None = None) -> None:
        self._settings = settings or IngestSettings()

    async def run(self, ctx: IngestContext) -> IngestOutcome:
        if ctx.secret_found:
            return await self._refuse_secret(ctx, ctx.secret_kinds)
        reader = ctx.memory.reader(ctx.scope)
        known = await reader.entities()
        me = await reader.self_entity()
        categories = await reader.categories()
        vocab = await reader.vocab()
        notes = _Notes(models={"extract": ctx.steps.model_of(Step.EXTRACT)})

        async with ctx.trail.run(AgentStep.EXTRACT):
            extraction = await self._extract(ctx, known, categories, vocab)
        secret_values = list(extraction.secret_spans)
        secrets = [m for m in extraction.memories if m.sensitivity == "secret"]
        memories = [m for m in extraction.memories if m.sensitivity != "secret"]
        if secrets and not memories:
            outcome = await self._refuse_secret(ctx, ["model-labelled"])
            outcome.secret_values = secret_values or [_secret_guess(m) for m in secrets]
            return outcome

        async with _step(ctx, AgentStep.ENTITIES, ran=bool(extraction.entities)):
            plans_by_ref = await resolve_entities(
                extraction.entities,
                known,
                me,
                now=ctx.now.instant,
                choose=self._entity_chooser(ctx, notes),
            )
        writer = ctx.memory.writer(
            ctx.scope,
            WriterTurn(
                turn_id=ctx.turn_id,
                workspace_id=ctx.scope.workspace_id,
                kind="user",
                now=ctx.now.instant,
            ),
            emit=ctx.emit,
        )
        plans = await self._plan_all(
            ctx, memories, plans_by_ref, notes, vocab=vocab, categories=categories, writer=writer
        )

        async with _step(ctx, AgentStep.RECONCILE, ran=bool(plans)):
            reconciler = Reconciler(
                reader,
                threshold=self._settings.reconcile_threshold,
                embed=self._embed_one(ctx),
                model=ctx.steps.embedding_model,
                choose=self._reconcile_chooser(ctx, notes),
            )
            for plan in plans:
                plan.decision = await reconciler.decide(plan.draft)

            relations = await self._relations(
                ctx, extraction, plans, plans_by_ref, notes=notes, vocab=vocab, writer=writer
            )

            ops = self._ops(ctx, plans, plans_by_ref, relations, writer)
            writer.add(*ops)
            for skipped in extraction.not_written:
                writer.note_not_written(skipped.what, skipped.reason, rule_id="MODEL")
            if secrets:
                writer.add(_secret_op(ctx))

            decision = _decision_event(plans, plans_by_ref, notes, extraction, secrets)
            await ctx.emit(decision)
        commit = await _commit(ctx, writer)

        async with _step(ctx, AgentStep.ENRICH, ran=bool(commit.touched_items)):
            await self._after_commit(ctx, commit, plans)
        facts = AckFacts(
            commit=commit,
            now=ctx.now,
            new_people=[p for p in plans_by_ref.values() if p.assumed_new_person],
            assumed_times=[t for t in notes.times if t.assumed],
            unresolved=notes.unresolved,
        )
        return IngestOutcome(
            reply=acknowledge(facts),
            commit=commit,
            decision=decision,
            secret_values=secret_values,
            renamed_entities=commit.renamed_entities,
        )

    async def _plan_all(
        self,
        ctx: IngestContext,
        memories: list[ProposedMemory],
        plans_by_ref: dict[str, EntityPlan],
        notes: _Notes,
        *,
        vocab: Sequence[Any],
        categories: Sequence[Any],
        writer: MemoryWriter,
    ) -> list[_Plan]:
        """Dates first (their own step on the Trail), then each memory's plan."""
        subtype_terms = terms_for(
            "subtype", [(v.slug, v.aliases) for v in vocab if v.vocab is VocabKind.SUBTYPE]
        )
        predicate_terms = terms_for(
            "predicate", [(v.slug, v.aliases) for v in vocab if v.vocab is VocabKind.PREDICATE]
        )
        category_terms = [Term(c.slug, tuple(c.aliases)) for c in categories]

        times: dict[str, dict[str, Resolved]] = {}
        async with _step(ctx, AgentStep.DATES, ran=any(m.times for m in memories)):
            for proposed in memories:
                times[proposed.ref] = _resolve_times(ctx, proposed, notes)
        plans: list[_Plan] = []
        for proposed in memories:
            plan = await self._plan(
                ctx,
                proposed,
                times[proposed.ref],
                plans_by_ref,
                notes,
                subtypes=subtype_terms,
                predicates=predicate_terms,
                categories=category_terms,
            )
            plans.append(plan)
            for vocab_kind, slug in (
                (VocabKind.SUBTYPE, plan.draft.content.subtype),
                (VocabKind.PREDICATE, plan.draft.content.predicate),
            ):
                if slug:
                    writer.register_vocab(vocab_kind, slug)
        return plans

    async def _relations(
        self,
        ctx: IngestContext,
        extraction: ExtractOutput,
        plans: list[_Plan],
        plans_by_ref: dict[str, EntityPlan],
        *,
        notes: _Notes,
        vocab: Sequence[Any],
        writer: MemoryWriter,
    ) -> list[RelateEntities]:
        relation_terms = terms_for(
            "relation", [(v.slug, v.aliases) for v in vocab if v.vocab is VocabKind.RELATION]
        )
        relations: list[RelateEntities] = []
        ids = _ids_by_ref(plans)
        for rel in extraction.relations:
            src, dst = plans_by_ref.get(rel.src), plans_by_ref.get(rel.dst)
            if src is None or dst is None or src.entity_id == dst.entity_id:
                continue
            slug, normalisation = await normalise(
                rel.relation, relation_terms, vocab="relation", similarity=ctx.steps.similarity
            )
            notes.normalisations.append(normalisation)
            writer.register_vocab(VocabKind.RELATION, slug)
            relations.append(
                RelateEntities(
                    relation_id=new_id(),
                    src_entity_id=src.entity_id,
                    relation=slug,
                    dst_entity_id=dst.entity_id,
                    evidence_item_id=ids.get(rel.evidence) if rel.evidence else None,
                    title=f"{src.name} · {slug} · {dst.name}",
                    rationale="stated relation between entities",
                )
            )
        return relations

    # ------------------------------------------------------------------ extraction

    async def _extract(
        self,
        ctx: IngestContext,
        known: Sequence[EntityRecord],
        categories: Sequence[Any],
        vocab: Sequence[Any],
    ) -> ExtractOutput:
        variables = ExtractVars(
            now=ctx.now.local.strftime("%A %Y-%m-%d %H:%M"),
            timezone=ctx.now.timezone,
            entities=_entities_context(known),
            categories="\n".join(f"- {c.slug}" for c in categories) or "- (none yet)",
            vocabulary=_vocab_context(vocab),
        )
        messages = [ChatMessage.user(ctx.message)]
        out = await ctx.steps.structured(Step.EXTRACT, ExtractOutput, variables, messages)
        errors = validate_extraction(out)
        if not errors:
            return out
        retry = [
            *messages,
            ChatMessage(role="assistant", content=out.model_dump_json()),
            ChatMessage.user(
                "That output has these problems. Return the whole output again, corrected:\n- "
                + "\n- ".join(errors)
            ),
        ]
        out = await ctx.steps.structured(Step.EXTRACT, ExtractOutput, variables, retry)
        errors = validate_extraction(out)
        if errors:
            log.warning("ingest.extraction_invalid", errors=len(errors))
            raise ExtractionInvalidError(errors)
        return out

    # ------------------------------------------------------------------ one memory

    async def _plan(
        self,
        ctx: IngestContext,
        m: ProposedMemory,
        resolved: dict[str, Resolved],
        entities: dict[str, EntityPlan],
        notes: _Notes,
        *,
        subtypes: list[Term],
        predicates: list[Term],
        categories: list[Term],
    ) -> _Plan:
        kind = Kind(m.kind)
        tz = ctx.now.timezone
        fields: dict[str, Any] = {}
        for r in resolved.values():
            if r.clock is TimeClock.OCCURRED and "occurred_start" not in fields:
                fields.update(
                    occurred_start=r.start,
                    occurred_end=r.end,
                    time_precision=r.precision,
                    rrule=validate_rrule(r.rrule, r.start) if r.rrule else None,
                )
            elif r.clock is TimeClock.VALID:
                fields["valid_to" if r.bound == "until" else "valid_from"] = r.start
                fields.setdefault("time_precision", r.precision)
            elif r.clock is TimeClock.DUE and "due_at" not in fields:
                fields["due_at"] = r.start
                fields.setdefault("time_precision", r.precision)

        subtype = None
        if m.subtype:
            subtype, normalisation = await normalise(m.subtype, subtypes, vocab="subtype")
            notes.normalisations.append(normalisation)
        predicate = None
        if m.predicate:
            predicate, normalisation = await normalise(m.predicate, predicates, vocab="predicate")
            notes.normalisations.append(normalisation)
        category_slug = None
        if m.category:
            category_slug, normalisation = await normalise(
                m.category,
                categories,
                vocab="category",
                similarity=ctx.steps.similarity if categories else None,
                path=True,
            )
            notes.normalisations.append(normalisation)
            if all(t.slug != category_slug for t in categories):
                categories.append(Term(category_slug))

        text = _fill_dates(m.text, m, resolved, tz)
        title = _fill_dates(m.title, m, resolved, tz)
        links: list[tuple[uuid.UUID, EntityRole, EntityKind]] = []
        for ref in m.entities:
            plan = entities.get(ref.entity)
            if plan is None or plan.kind is EntityKind.SELF:
                continue
            links.append((plan.entity_id, EntityRole(ref.role), plan.kind))
        subject = entities.get(m.subject) if m.subject else None
        value = None
        if m.value is not None and (m.value.text or m.value.number is not None):
            value = {"text": m.value.text, "number": m.value.number, "unit": m.value.unit}
        state = m.state or initial_state(kind)
        content = ItemContent(
            kind=kind,
            subtype=subtype,
            format=ResourceFormat(m.format) if m.format and kind is Kind.RESOURCE else None,
            state=state,
            text=text,
            title=title,
            summary=m.summary,
            tags=sorted({t.strip().lower() for t in m.tags if t.strip()}),
            attributes={a.key: a.value for a in m.attributes if a.key},
            enrichment={
                "times": [
                    {"expression": r.expression, "clock": r.clock.value, "rule": r.rule}
                    for r in resolved.values()
                ]
            },
            rationale=" ".join(x for x in (m.rationale, m.layer_rationale) if x) or None,
            raw_content=ctx.message,
            subject_entity_id=subject.entity_id if subject else None,
            predicate=predicate,
            value=value,
            mentioned_at=ctx.now.instant,
            modality=Modality(m.modality),
            sentiment=m.sentiment,
            rating={"value": m.rating.value, "scale": m.rating.scale} if m.rating else None,
            sensitivity=Sensitivity(m.sensitivity),
            importance=m.importance,
            **fields,
        )
        triggers = self._triggers(ctx, m, content, resolved, notes)
        triggers += self._moment_trigger(m, entities, notes)
        quick = quick_layer(
            content,
            now=ctx.now.instant,
            triggers=[t.trigger for t in triggers],
            horizon_days=self._settings.quick_horizon_days,
            recent_days=self._settings.quick_recent_days,
        )
        content = content.model_copy(
            update={
                "in_quick": quick.in_quick,
                "quick_reason": quick.reason,
                "quick_until": quick.until,
            }
        )
        about_key = any(
            entities[r.entity].is_key
            for r in m.entities
            if r.entity in entities and r.role == "about"
        ) or bool(subject and subject.is_key)
        core = m.layer == "core" or (kind in (Kind.FACT, Kind.PREFERENCE) and about_key)
        draft = Draft(ref=m.ref, item_id=new_id(), content=content, entities=links)
        return _Plan(
            proposed=m,
            draft=draft,
            title=title,
            category_slug=category_slug,
            triggers=triggers,
            core=core and content.modality is not Modality.HYPOTHETICAL,
        )

    def _triggers(
        self,
        ctx: IngestContext,
        m: ProposedMemory,
        content: ItemContent,
        resolved: dict[str, Resolved],
        notes: _Notes,
    ) -> list[NewTrigger]:
        explicit = [r for r in resolved.values() if r.clock is TimeClock.TRIGGER]
        if m.reminder is None and not explicit:
            return []
        tz = ctx.now.timezone
        if explicit:
            fires = explicit[0].start
            if explicit[0].precision is not TimePrecision.DATETIME:
                fires = reminder_time(explicit[0], explicit[0].precision, timedelta(0), tz)
            spec: dict[str, Any] = {"rule": "explicit", "expression": explicit[0].expression}
        else:
            anchor = content.occurred_start or content.due_at
            if anchor is None:
                notes.unresolved.append("a reminder with no date to remind you about")
                return []
            lead_minutes = ctx.default_lead_minutes
            lead_rule = "workspace default"
            if m.reminder is not None and m.reminder.lead:
                try:
                    lead = resolve_lead(m.reminder.lead)
                    lead_minutes, lead_rule = int(lead.delta.total_seconds() // 60), lead.rule
                except UnresolvableTimeError:
                    notes.unresolved.append(m.reminder.lead)
            precision = content.time_precision or TimePrecision.DAY
            fires = reminder_time(anchor, precision, timedelta(minutes=lead_minutes), tz)
            spec = {"lead_minutes": lead_minutes, "rule": lead_rule}
        return [
            NewTrigger(
                trigger_id=new_id(),
                trigger=TriggerContent(on=TriggerOn.TIME, spec=spec, fires_at=fires),
            )
        ]

    @staticmethod
    def _moment_trigger(
        m: ProposedMemory, entities: dict[str, EntityPlan], notes: _Notes
    ) -> list[NewTrigger]:
        """``on: person`` (the entity is matched every turn) or ``on: topic``/``situation``
        (the cue is embedded as a key and compared with each message) (S3.10)."""
        t = m.trigger
        if t is None:
            return []
        if t.on == "person":
            plan = entities.get(t.entity or "")
            if plan is None or plan.kind is not EntityKind.PERSON:
                notes.unresolved.append("a reminder for a person I couldn't tell")
                return []
            spec: dict[str, Any] = {"entity_id": str(plan.entity_id), "name": plan.name}
        else:
            cue = " ".join((t.cue or "").split())
            if not cue:
                notes.unresolved.append(f"a reminder for a {t.on} with nothing to recognise it by")
                return []
            spec = {"cue": cue, "cue_hash": content_hash(cue)}
        return [
            NewTrigger(
                trigger_id=new_id(),
                trigger=TriggerContent(on=TriggerOn(t.on), spec=spec, fires_at=None),
            )
        ]

    # ------------------------------------------------------------------ ops

    def _ops(
        self,
        ctx: IngestContext,
        plans: list[_Plan],
        entities: dict[str, EntityPlan],
        relations: list[RelateEntities],
        writer: Any,
    ) -> list[Op]:
        entity_ops: list[Op] = [p.op for p in entities.values() if p.op is not None]
        creates: list[Op] = []
        changes: list[Op] = []
        promotions: list[Op] = []
        ids = _ids_by_ref(plans)
        for plan in plans:
            decision = plan.decision or Decision(ReconcileDecision.NEW)
            draft = plan.draft
            info = decision.info
            candidate = decision.candidate
            if decision.decision is ReconcileDecision.NO_OP and candidate is not None:
                writer.note_not_written(
                    plan.title,
                    f"already saved as '{candidate.title}'",
                    reconcile=info,
                    item_id=candidate.id,
                )
                continue
            if decision.decision is ReconcileDecision.ADD_DETAIL and candidate is not None:
                changes.extend(_add_detail(candidate, draft, plan.title, info))
                continue
            creates.append(
                CreateItem(
                    item_id=draft.item_id,
                    item=draft.content,
                    entities=[
                        EntityLink(row_id=new_id(), entity_id=e, role=role)
                        for e, role, _ in draft.entities
                    ],
                    triggers=plan.triggers,
                    category_slug=plan.category_slug,
                    title=plan.title,
                    rationale=draft.content.rationale or "",
                    reconcile=info,
                )
            )
            if decision.decision is ReconcileDecision.SUPERSEDE and candidate is not None:
                valid_to = draft.content.valid_from or ctx.now.instant
                changes.append(
                    SupersedeItem(
                        old_id=candidate.id,
                        new_id=draft.item_id,
                        link_id=new_id(),
                        valid_to=valid_to,
                        state="moved" if candidate.kind is Kind.PLAN else "superseded",
                        title=candidate.title,
                        rationale=decision.rule,
                        reconcile=info,
                    )
                )
            elif decision.decision is ReconcileDecision.FULFIL and candidate is not None:
                changes.append(
                    FulfilIntention(
                        intention_id=candidate.id,
                        episode_id=draft.item_id,
                        link_id=new_id(),
                        title=candidate.title,
                        rationale=decision.rule,
                        reconcile=info,
                    )
                )
            elif decision.decision is ReconcileDecision.LINK and candidate is not None:
                changes.append(
                    LinkItems(
                        link_id=new_id(),
                        src_id=draft.item_id,
                        link_type=LinkType.FOLLOWS,
                        dst_id=candidate.id,
                        title=f"{plan.title} → {candidate.title}",
                        rationale=decision.rule,
                        reconcile=info,
                    )
                )
            if plan.core:
                promotions.append(
                    UpdateItem(
                        item_id=draft.item_id,
                        changes={"in_core": True},
                        title=plan.title,
                        rationale="a standing fact or preference about you or a key person",
                    )
                )
        links: list[Op] = []
        for plan in plans:
            for link in plan.proposed.links:
                src, dst = ids.get(plan.proposed.ref), ids.get(link.to)
                if src and dst and src != dst:
                    links.append(
                        LinkItems(
                            link_id=new_id(),
                            src_id=src,
                            link_type=LinkType(link.type),
                            dst_id=dst,
                            title=f"{plan.title} · {link.type}",
                        )
                    )
        return [*entity_ops, *creates, *changes, *links, *relations, *promotions]

    # ------------------------------------------------------------------ after the commit

    async def _after_commit(
        self, ctx: IngestContext, commit: CommitResult, plans: list[_Plan]
    ) -> None:
        created = {p.draft.item_id: p for p in plans if p.draft.item_id in commit.created_items}
        extra: dict[uuid.UUID, list[tuple[KeyKind, str]]] = {}
        if created and self._settings.enrich_enabled:
            try:
                extra = await self._enrich(ctx, created)
            except ProviderUnavailableError:
                log.warning("ingest.enrich_unavailable")
        for item_id, plan in created.items():
            cues = [str(t.trigger.spec["cue"]) for t in plan.triggers if t.trigger.spec.get("cue")]
            extra.setdefault(item_id, []).extend((KeyKind.CUE, c) for c in cues)
        if not commit.touched_items:
            return
        indexer = ctx.memory.keys(
            ctx.scope,
            timezone=ctx.now.timezone,
            embed=self._embedder(ctx),
            model=ctx.steps.embedding_model,
        )
        await indexer.rebuild(sorted(commit.touched_items), extra=extra)

    async def _enrich(
        self, ctx: IngestContext, created: dict[uuid.UUID, _Plan]
    ) -> dict[uuid.UUID, list[tuple[KeyKind, str]]]:
        by_ref = {p.draft.ref: item_id for item_id, p in created.items()}
        listing = [
            {
                "ref": p.draft.ref,
                "kind": p.draft.content.kind.value,
                "subtype": p.draft.content.subtype,
                "title": p.title,
                "text": p.draft.content.text,
            }
            for p in created.values()
        ]
        out = await ctx.steps.structured(
            Step.ENRICH,
            EnrichOutput,
            EnrichVars(cues_max=self._settings.cue_keys_max),
            [ChatMessage.user(json.dumps(listing, ensure_ascii=False))],
        )
        extra: dict[uuid.UUID, list[tuple[KeyKind, str]]] = {}
        for entry in out.memories:
            item_id = by_ref.get(entry.ref)
            if item_id is None:
                continue
            keys = [(KeyKind.ALT, a) for a in entry.alt[:3]]
            keys += [(KeyKind.CUE, c) for c in entry.cues[: self._settings.cue_keys_max]]
            extra[item_id] = keys
        return extra

    def _embedder(
        self, ctx: IngestContext
    ) -> Callable[[Sequence[str], int], Awaitable[list[list[float]] | None]]:
        async def embed(texts: Sequence[str], hits: int) -> list[list[float]] | None:
            try:
                return await ctx.steps.embed(texts, hits)
            except ProviderUnavailableError:
                log.warning("ingest.embed_unavailable")
                return None

        return embed

    def _embed_one(self, ctx: IngestContext) -> Callable[[str], Awaitable[list[float] | None]]:
        async def embed(text: str) -> list[float] | None:
            try:
                return await ctx.steps.embed_one(text)
            except ProviderUnavailableError:
                return None

        return embed

    # ------------------------------------------------------------------ model choices

    def _entity_chooser(
        self, ctx: IngestContext, notes: _Notes
    ) -> Callable[[ExtractedEntity, Sequence[EntityRecord]], Awaitable[tuple[int | None, str]]]:
        async def choose(
            x: ExtractedEntity, candidates: Sequence[EntityRecord]
        ) -> tuple[int | None, str]:
            listing = "\n".join(
                f"- c{i + 1}: {c.display} ({c.kind.value}); aliases: {', '.join(c.aliases) or '-'}"
                for i, c in enumerate(candidates)
            )
            notes.models["resolve"] = ctx.steps.model_of(Step.RESOLVE)
            try:
                out = await ctx.steps.structured(
                    Step.RESOLVE,
                    EntityChoice,
                    ResolveVars(mention=x.mention, candidates=listing),
                    [ChatMessage.user(ctx.message)],
                )
            except ProviderUnavailableError:
                return 0, "model unavailable, took the most recent"
            index = _candidate_index(out.candidate, len(candidates))
            return index, out.reason

        return choose

    def _reconcile_chooser(
        self, ctx: IngestContext, notes: _Notes
    ) -> Callable[
        [Draft, Sequence[tuple[ItemRecord, float | None]]],
        Awaitable[tuple[ReconcileDecision, int | None, str]],
    ]:
        async def choose(
            draft: Draft, candidates: Sequence[tuple[ItemRecord, float | None]]
        ) -> tuple[ReconcileDecision, int | None, str]:
            proposed = _describe(draft.content, ctx.now.timezone)
            listing = "\n".join(
                f"- c{i + 1}: {_describe(item.content, ctx.now.timezone)}"
                + (f" (similarity {score:.2f})" if score is not None else "")
                for i, (item, score) in enumerate(candidates)
            )
            notes.models["reconcile"] = ctx.steps.model_of(Step.RECONCILE)
            try:
                out = await ctx.steps.structured(
                    Step.RECONCILE,
                    ReconcileChoice,
                    ReconcileVars(proposed=proposed, candidates=listing),
                    [ChatMessage.user(ctx.message)],
                )
            except ProviderUnavailableError:
                return ReconcileDecision.NEW, None, "model unavailable, kept it separate"
            index = _candidate_index(out.candidate, len(candidates))
            return ReconcileDecision(out.decision), index, out.reason

        return choose

    # ------------------------------------------------------------------ secrets

    async def _refuse_secret(self, ctx: IngestContext, kinds: Sequence[str]) -> IngestOutcome:
        writer = ctx.memory.writer(
            ctx.scope,
            WriterTurn(
                turn_id=ctx.turn_id,
                workspace_id=ctx.scope.workspace_id,
                kind="user",
                now=ctx.now.instant,
            ),
            emit=ctx.emit,
        )
        writer.add(_secret_op(ctx))
        decision = DecisionEvent(
            summary="A secret: refused, nothing written.",
            classifications=[
                Classification(
                    label=_SECRET_TITLE,
                    kind=Kind.FACT,
                    sensitivity=Sensitivity.SECRET,
                    rationale="secret check matched: " + (", ".join(kinds) or "secret"),
                )
            ],
            rules_applied=["P-SECRET-1"],
            rationale=(
                "Secrets are refused, not stored. The deterministic pre-check ran before any model "
                "call, so the secret was never sent to a provider."
                if ctx.secret_found
                else "The extraction labelled this a secret; it was refused and redacted."
            ),
        )
        await ctx.emit(decision)
        commit = await _commit(ctx, writer)
        return IngestOutcome(reply=refusal(), commit=commit, decision=decision)


# ------------------------------------------------------------------ helpers


def _resolve_times(ctx: IngestContext, m: ProposedMemory, notes: _Notes) -> dict[str, Resolved]:
    """Resolve a memory's time expressions by code (the Trail's dates step)."""
    kind = Kind(m.kind)
    resolved: dict[str, Resolved] = {}
    for t in m.times:
        try:
            r = resolve(
                t.expression,
                TimeClock(t.clock),
                ctx.now,
                direction=t.direction
                or ("past" if t.clock == "occurred" and kind is Kind.EPISODE else "auto"),
                recurring=t.recurring,
            )
        except UnresolvableTimeError:
            notes.unresolved.append(t.expression)
            continue
        resolved[t.id] = r
        notes.times.append(_time_event(r, ctx.now, m.ref))
    return resolved


@asynccontextmanager
async def _no_step(step: AgentStep) -> AsyncIterator[StepRun]:
    yield StepRun(step=step, started_at=utc_now())


def _step(
    ctx: IngestContext, step: AgentStep, *, ran: bool
) -> AbstractAsyncContextManager[StepRun]:
    """The step on the Trail when it has work to do; otherwise it isn't reported at all."""
    return ctx.trail.run(step) if ran else _no_step(step)


_CHANGES = frozenset({"added", "updated", "removed", "superseded", "fulfilled"})


def _guard_status(commit: CommitResult) -> StepStatus:
    verdicts = [o.verdict for o in commit.outcomes]
    if any(
        v.decision is PolicyDecision.BLOCKED and v.rule_id != "NOT-APPLICABLE" for v in verdicts
    ):
        return StepStatus.REFUSED
    if any(v.decision is PolicyDecision.HELD for v in verdicts):
        return StepStatus.HELD
    return StepStatus.DONE


async def _commit(ctx: IngestContext, writer: MemoryWriter) -> CommitResult:
    """Commit the writer as two Trail steps: ``guard`` (the policy verdicts) and ``save`` (the
    writes). They share one transaction, so the split is measured inside it; ``save`` is
    reported only when something was actually saved."""
    began = time.perf_counter()
    async with ctx.trail.run(AgentStep.GUARD) as guard:
        commit = await writer.commit()
        total_ms = (time.perf_counter() - began) * 1000
        guard.status = _guard_status(commit)
        guard.latency_ms = round(commit.policy_ms)
        saved = any(e.op in _CHANGES for e in commit.diff.entries)
        diff = guard.take(lambda e: e.type == "memory_diff") if saved else []
    if saved:
        await ctx.trail.report(
            AgentStep.SAVE,
            status=StepStatus.DONE,
            started_at=guard.started_at + timedelta(milliseconds=commit.policy_ms),
            latency_ms=round(total_ms - commit.policy_ms),
            events=diff,
        )
    return commit


def _secret_op(ctx: IngestContext) -> CreateItem:
    return CreateItem(
        item_id=new_id(),
        item=ItemContent(
            kind=Kind.FACT,
            state="current",
            text="[redacted secret]",
            title=_SECRET_TITLE,
            mentioned_at=ctx.now.instant,
            sensitivity=Sensitivity.SECRET,
        ),
        title=_SECRET_TITLE,
        rationale="secret",
    )


def _secret_guess(m: ProposedMemory) -> str:
    return m.text


def _ids_by_ref(plans: Sequence[_Plan]) -> dict[str, uuid.UUID]:
    out: dict[str, uuid.UUID] = {}
    for plan in plans:
        decision = plan.decision
        if decision and decision.decision in (
            ReconcileDecision.NO_OP,
            ReconcileDecision.ADD_DETAIL,
        ):
            if decision.candidate is not None:
                out[plan.draft.ref] = decision.candidate.id
        else:
            out[plan.draft.ref] = plan.draft.item_id
    return out


def _add_detail(candidate: ItemRecord, draft: Draft, title: str, info: Any) -> list[Op]:
    new = draft.content
    tags = sorted(set(candidate.tags) | set(new.tags))
    attributes = {**new.attributes, **candidate.attributes}
    changes: dict[str, Any] = {}
    if tags != candidate.tags:
        changes["tags"] = tags
    if attributes != candidate.attributes:
        changes["attributes"] = attributes
    if new.summary and not candidate.summary:
        changes["summary"] = new.summary
    ops: list[Op] = []
    if changes:
        ops.append(
            UpdateItem(
                item_id=candidate.id,
                changes=changes,
                title=candidate.title,
                rationale="add detail to the stored memory",
                reconcile=info,
            )
        )
    return ops


def _fill_dates(text: str, m: ProposedMemory, resolved: dict[str, Resolved], tz: str) -> str:
    """Write resolved dates where the model put placeholders (it never writes dates itself)."""

    def value(time_id: str) -> str:
        r = resolved.get(time_id)
        if r is None:
            return "(date unclear)"
        if r.rrule:
            return rrule_words(r.rrule, tz, r.start)
        part = r.precision is TimePrecision.DATETIME and r.part_of_day is not None
        return format_when(r.start, r.precision, tz, part=part)

    filled = _PLACEHOLDER.sub(lambda match: value(match.group(1)), text)
    for t in m.times:
        if t.id in resolved and t.expression and t.expression in filled:
            filled = filled.replace(t.expression, value(t.id))
    if has_relative_time(filled):
        first = next(iter(resolved), None)
        replacement = value(first) if first else ""
        filled = RELATIVE_WORDS.sub(replacement, filled)
    return " ".join(filled.split())


def _time_event(r: Resolved, now: TurnNow, ref: str) -> TimeResolution:
    local = r.start.astimezone(now.local.tzinfo)
    if r.precision is TimePrecision.DATETIME:
        value = local.strftime("%Y-%m-%dT%H:%M")
    elif r.precision is TimePrecision.MONTH:
        value = local.strftime("%Y-%m")
    elif r.precision is TimePrecision.YEAR:
        value = local.strftime("%Y")
    else:
        value = local.date().isoformat()
    end = None
    if r.end is not None:
        end = r.end.astimezone(now.local.tzinfo).strftime("%Y-%m-%dT%H:%M")
    return TimeResolution(
        expression=r.expression,
        clock=r.clock,
        value=value,
        end=end,
        precision=r.precision,
        rrule=r.rrule,
        now=now.instant,
        timezone=now.timezone,
        rule=r.rule,
        assumed=r.assumed,
        alternative=r.alternative,
        memory=ref,
    )


def _decision_event(
    plans: Sequence[_Plan],
    entities: dict[str, EntityPlan],
    notes: _Notes,
    extraction: ExtractOutput,
    secrets: Sequence[ProposedMemory],
) -> DecisionEvent:
    classifications = [
        Classification(
            label=p.title,
            kind=p.draft.content.kind,
            subtype=p.draft.content.subtype,
            format=p.draft.content.format.value if p.draft.content.format else None,
            state=p.draft.content.state,
            category=p.category_slug,
            modality=p.draft.content.modality,
            sensitivity=p.draft.content.sensitivity,
            layer=Layer.CORE
            if p.core
            else (Layer.QUICK if p.draft.content.in_quick else Layer.ARCHIVE),
            rationale=p.draft.content.rationale or "",
        )
        for p in plans
    ]
    classifications.extend(
        Classification(
            label=_SECRET_TITLE,
            kind=Kind(m.kind),
            sensitivity=Sensitivity.SECRET,
            rationale="labelled a secret; refused",
        )
        for m in secrets
    )
    kinds = [
        f"{p.draft.content.kind.value}"
        + (f" ({p.draft.content.subtype})" if p.draft.content.subtype else "")
        for p in plans
    ]
    new_entities = [e for e in entities.values() if e.resolution.created]
    summary = f"{len(plans)} memor{'y' if len(plans) == 1 else 'ies'}: " + (
        " · ".join(kinds) or "none"
    )
    if new_entities:
        summary += f"; {len(new_entities)} new entit{'y' if len(new_entities) == 1 else 'ies'}"
    resolutions: list[EntityResolution] = [
        e.resolution for ref, e in entities.items() if ref != "self"
    ]
    return DecisionEvent(
        summary=summary,
        classifications=classifications,
        time_resolutions=notes.times,
        entity_resolutions=resolutions,
        normalisations=notes.normalisations,
        reconciliations=[
            Reconciliation(label=p.title, info=p.decision.info) for p in plans if p.decision
        ],
        not_written=[f"{n.what}: {n.reason}" for n in extraction.not_written]
        + [f"couldn't resolve the time '{u}'" for u in notes.unresolved],
        rules_applied=sorted({p.decision.rule for p in plans if p.decision and p.decision.rule}),
        rationale=" ".join(p.draft.content.rationale or "" for p in plans).strip(),
        decided_by=notes.models,
    )


def _entities_context(known: Sequence[EntityRecord]) -> str:
    lines = [
        f"- {e.display} [{e.kind.value}]"
        + (f" aliases: {', '.join(e.aliases)}" if e.aliases else "")
        for e in known
        if e.kind is not EntityKind.SELF
    ]
    return "\n".join(lines[:200]) or "- (none yet)"


def _vocab_context(vocab: Sequence[Any]) -> str:
    by_kind: dict[str, list[str]] = {}
    for term in vocab:
        by_kind.setdefault(term.vocab.value, []).append(term.slug)
    if not by_kind:
        return "- (none yet)"
    return "\n".join(
        f"- {kind}: {', '.join(sorted(slugs))}" for kind, slugs in sorted(by_kind.items())
    )


def _describe(content: ItemContent, tz: str) -> str:
    when = ""
    if content.occurred_start is not None:
        when = f", {format_when(content.occurred_start, content.time_precision, tz)}"
    kind = content.kind.value + (f"/{content.subtype}" if content.subtype else "")
    return f"[{kind}] {content.text}{when}"


def _candidate_index(label: str | None, count: int) -> int | None:
    if not label:
        return None
    match = re.fullmatch(r"c?(\d+)", label.strip().lower())
    if not match:
        return None
    index = int(match.group(1)) - 1
    return index if 0 <= index < count else None

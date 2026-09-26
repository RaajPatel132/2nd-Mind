"""Correct by chat, and edit in place (S3.12, ADR-0027).

A correction means one of three things, and each is written differently:

* **reclassify** — how a memory was filed was wrong (kind, subtype, category, tags, format,
  layer, state, or a date resolved wrongly): an ``update`` with before -> after in the diff. A
  new date comes from the person's words, through the resolver.
* **wrong value** — what it says was never true: a new row ``corrects`` the old one, which is
  archived as a *mistake* (not history), and a wrong relation is replaced.
* **forget** — a soft delete, which ``P-BULK-1`` holds for confirmation.

A **bulk** re-file changes every match; over ``POLICY_BULK_THRESHOLD`` it's held. A "yes" to
the count cross-check's offer re-files the look-alikes it named. A glass-box edit
(``PATCH /v1/items/{id}``) is its own turn with origin ``ui_edit``. Everything goes through
``MemoryWriter`` and policy, so the diff shows it and undo reverses it; keys are re-rendered.
"""

import json
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from secondmind.config import Step
from secondmind.core import (
    AgentStep,
    Kind,
    TimeClock,
    TimePrecision,
    Trail,
    TriggerState,
    WorkspaceScope,
    initial_state,
    new_id,
    valid_state,
)
from secondmind.corrections.schemas import CorrectionChanges, CorrectionOut
from secondmind.ingestion import (
    ModelSteps,
    TurnNow,
    UnresolvableTimeError,
    match_entities,
    resolve,
    slugify,
    summarise_commit,
)
from secondmind.memory import (
    CommitResult,
    CorrectItem,
    CreateItem,
    DeleteItem,
    EntityLink,
    ItemContent,
    ItemRecord,
    Memory,
    Op,
    RelateEntities,
    UnrelateEntities,
    UpdateItem,
    UpdateTrigger,
    WriterTurn,
)
from secondmind.providers import ChatMessage, ProviderUnavailableError
from secondmind.retrieval import Access, Filters, Query, RecallStore

Write = Callable[[str], None]
EmbedTexts = Callable[[Sequence[str], int], Awaitable[list[list[float]] | None]]

# How many memories a bulk re-file looks at; the policy holds anything over its threshold.
BULK_LIMIT = 50
_DATE_FIELDS = {
    "occurred": ("occurred_start", "occurred_end"),
    "due": ("due_at", None),
    "valid": ("valid_from", None),
}


class CorrectVars(BaseModel):
    now: str
    timezone: str
    previous: str
    candidates: str
    vocabulary: str


@dataclass(frozen=True, slots=True)
class Offer:
    """A "yes" to the count cross-check: re-file these items with this fix (S3.6)."""

    item_ids: tuple[uuid.UUID, ...]
    fix: Mapping[str, str]
    label: str


@dataclass(slots=True)
class CorrectContext:
    scope: WorkspaceScope
    turn_id: uuid.UUID
    message: str
    now: TurnNow
    steps: ModelSteps
    memory: Memory
    store: RecallStore
    trail: Trail
    write: Write
    previous_items: Sequence[uuid.UUID] = ()
    offer: Offer | None = None
    origin: str = "user_message"
    embed: EmbedTexts | None = None


@dataclass(slots=True)
class CorrectOutcome:
    reply: str
    kind: str
    commit: CommitResult | None = None
    notes: list[str] = field(default_factory=list)


class Corrector:
    async def run(self, ctx: CorrectContext) -> CorrectOutcome:  # noqa: PLR0911
        if ctx.offer is not None:
            async with ctx.trail.run(AgentStep.UNDERSTAND):
                ops = await self._offer_ops(ctx, ctx.offer)
            return await self._commit(ctx, ops, "reclassify", [], prefix="Re-filed")
        async with ctx.trail.run(AgentStep.SEARCH):
            previous, found = await self._targets(ctx)
            out = await self._ask(ctx, previous, found)
        target = self._pick(out, previous, found)
        notes = [out.assumption] if out.assumption and target is not None else []
        match out.type:
            case "none":
                return self._say(ctx, "none", out.reason or "I couldn't tell what to change.")
            case "bulk":
                ops, extra = await self._bulk(ctx, out)
                return await self._commit(ctx, ops, "bulk", notes + extra, prefix="Re-filed")
        if target is None:
            return self._say(ctx, out.type, "I couldn't find the memory you mean; can you name it?")
        match out.type:
            case "reclassify":
                ops = await self._reclassify(ctx, target, out.changes, notes)
                return await self._commit(ctx, ops, "reclassify", notes, prefix="Fixed")
            case "wrong_value":
                ops = await self._wrong_value(ctx, target, out, notes)
                return await self._commit(ctx, ops, "wrong_value", notes, prefix="Corrected")
            case _:
                ops = [DeleteItem(item_id=target.id, title=target.title, rationale=out.reason)]
                return await self._commit(ctx, ops, "forget", notes, prefix="Forgot")

    async def edit(
        self,
        ctx: CorrectContext,
        item_id: uuid.UUID,
        changes: CorrectionChanges,
        *,
        delete: bool = False,
    ) -> CorrectOutcome:
        """A glass-box edit of one memory: no model call; its own turn (origin ``ui_edit``)."""
        item = await ctx.memory.reader(ctx.scope).item(item_id)
        if item is None:
            return self._say(ctx, "edit", "That memory doesn't exist any more.")
        if delete:
            ops: list[Op] = [DeleteItem(item_id=item.id, title=item.title, rationale="edited")]
        else:
            notes: list[str] = []
            ops = await self._reclassify(ctx, item, changes, notes)
            if notes and not ops:
                return self._say(ctx, "edit", " ".join(notes))
        return await self._commit(ctx, ops, "edit", [], prefix="Edited")

    async def snooze(
        self, ctx: CorrectContext, trigger_id: uuid.UUID, date_expression: str
    ) -> CorrectOutcome:
        """Move a pending reminder to a new time from Upcoming (S3.14). The memory's own date
        stays: snoozing a reminder isn't rescheduling what it's about."""
        reader = ctx.memory.reader(ctx.scope)
        trigger = await reader.trigger(trigger_id)
        if trigger is None or trigger.state is not TriggerState.PENDING:
            return self._say(ctx, "snooze", "That reminder isn't pending any more.")
        item = await reader.item(trigger.item_id)
        title = item.title if item is not None else "reminder"
        try:
            r = resolve(date_expression, TimeClock.OCCURRED, ctx.now)
        except UnresolvableTimeError:
            return self._say(ctx, "snooze", f"I couldn't work out the date '{date_expression}'.")
        op = UpdateTrigger(
            trigger_id=trigger.id,
            changes={"fires_at": r.start},
            title=f"reminder: {title}",
            rationale=f"snoozed to {date_expression}",
        )
        return await self._commit(ctx, [op], "snooze", [], prefix="Snoozed")

    # ------------------------------------------------------------------ target

    async def _targets(self, ctx: CorrectContext) -> tuple[list[ItemRecord], list[ItemRecord]]:
        reader = ctx.memory.reader(ctx.scope)
        previous = [i for i in await reader.items(list(ctx.previous_items)) if i.status == "active"]
        vectors = None
        try:
            vectors = await ctx.steps.embed([ctx.message])
        except ProviderUnavailableError:
            vectors = None
        hits = await ctx.store.search(
            Query(ctx.message, vectors[0] if vectors else None, ctx.steps.embedding_model),
            Filters(),
            Access(),
            limit=5,
        )
        found = await reader.items([h.item_id for h in hits])
        order = {h.item_id: h.rank for h in hits}
        found.sort(key=lambda i: order.get(i.id, 99))
        return previous, [i for i in found if i.id not in {p.id for p in previous}]

    async def _ask(
        self, ctx: CorrectContext, previous: Sequence[ItemRecord], found: Sequence[ItemRecord]
    ) -> CorrectionOut:
        vocab = await ctx.memory.reader(ctx.scope).vocab()
        return await ctx.steps.structured(
            Step.CORRECT,
            CorrectionOut,
            CorrectVars(
                now=ctx.now.local.strftime("%A %Y-%m-%d %H:%M"),
                timezone=ctx.now.timezone,
                previous=_listing("p", previous) or "(nothing)",
                candidates=_listing("c", found) or "(nothing)",
                vocabulary=", ".join(sorted({f"{v.vocab.value}:{v.slug}" for v in vocab})),
            ),
            [ChatMessage.user(ctx.message)],
        )

    @staticmethod
    def _pick(
        out: CorrectionOut, previous: Sequence[ItemRecord], found: Sequence[ItemRecord]
    ) -> ItemRecord | None:
        ref = (out.target.ref or "").strip().lower()
        pool = {f"p{n}": i for n, i in enumerate(previous, start=1)}
        pool |= {f"c{n}": i for n, i in enumerate(found, start=1)}
        if ref in pool:
            return pool[ref]
        if out.target.source == "previous_turn" and len(previous) == 1:
            return previous[0]
        return None

    # ------------------------------------------------------------------ changes

    async def _reclassify(  # noqa: PLR0912
        self,
        ctx: CorrectContext,
        item: ItemRecord,
        changes: CorrectionChanges | None,
        notes: list[str],
    ) -> list[Op]:
        if changes is None:
            notes.append("I couldn't tell what to change.")
            return []
        update: dict[str, Any] = {}
        kind = item.kind
        if changes.kind:
            try:
                kind = Kind(changes.kind)
            except ValueError:
                notes.append(f"'{changes.kind}' isn't a kind I know.")
            else:
                update["kind"] = kind
        if changes.subtype is not None:
            update["subtype"] = slugify(changes.subtype) or None
        if changes.category:
            update["category_slug"] = "/".join(
                slugify(p) for p in changes.category.split("/") if p.strip()
            )
        if changes.tags is not None:
            update["tags"] = sorted({t.strip().lower() for t in changes.tags if t.strip()})
        if changes.format:
            update["format"] = changes.format
        if changes.layer:
            update |= {
                "core": {"in_core": True},
                "quick": {"in_quick": True},
                "archive": {"in_core": False, "in_quick": False},
            }[changes.layer]
        state = changes.state or item.state
        if not valid_state(kind, state):
            state = initial_state(kind)
        if state != item.state:
            update["state"] = state
        if changes.date_expression:
            clock = changes.date_clock or ("due" if kind is Kind.TASK else "occurred")
            try:
                r = resolve(changes.date_expression, TimeClock(clock), ctx.now)
            except UnresolvableTimeError:
                notes.append(f"I couldn't work out the date '{changes.date_expression}'.")
            else:
                start, end = _DATE_FIELDS[clock]
                update[start] = r.start
                if end:
                    update[end] = r.end
                update["time_precision"] = r.precision
        if not update:
            return []
        return [UpdateItem(item_id=item.id, changes=update, title=item.title, origin=ctx.origin)]  # type: ignore[arg-type]

    async def _wrong_value(
        self, ctx: CorrectContext, old: ItemRecord, out: CorrectionOut, notes: list[str]
    ) -> list[Op]:
        reader = ctx.memory.reader(ctx.scope)
        text = " ".join((out.new_text or "").split())
        if not text:
            notes.append("I couldn't tell what the right version is.")
            return []
        rows = await reader.item_entities([old.id])
        new_id_ = new_id()
        content = old.model_dump(include=set(ItemContent.model_fields))
        content |= {"text": text, "title": text if len(text) <= 80 else old.title}
        ops: list[Op] = [
            CreateItem.model_validate(
                {
                    "item_id": new_id_,
                    "item": content,
                    "title": content["title"],
                    "entities": [
                        EntityLink(row_id=new_id(), entity_id=r.entity_id, role=r.role)
                        for r in rows
                    ],
                }
            ),
            CorrectItem(old_id=old.id, new_id=new_id_, link_id=new_id(), title=old.title),
        ]
        if out.relation is not None:
            ops += await self._relation(ctx, out, new_id_, notes)
        return ops

    async def _relation(
        self, ctx: CorrectContext, out: CorrectionOut, evidence: uuid.UUID, notes: list[str]
    ) -> list[Op]:
        fix = out.relation
        assert fix is not None  # noqa: S101 - checked by the caller
        reader = ctx.memory.reader(ctx.scope)
        entities = await reader.entities()
        me = await reader.self_entity()
        subject = match_entities(fix.subject, entities, me)
        obj = match_entities(fix.object, entities, me)
        if not subject or not obj:
            notes.append("I couldn't tell who the relation is between.")
            return []
        a, b = subject[0].id, obj[0].id
        wrong = slugify(fix.wrong)
        ops: list[Op] = []
        for r in await reader.relations([a]):
            between = r.valid_to is None and {r.src_entity_id, r.dst_entity_id} == {a, b}
            if between and (r.relation == wrong or not wrong):
                ops.append(UnrelateEntities(relation_id=r.id, title=r.relation))
        ops.append(
            RelateEntities(
                relation_id=new_id(),
                src_entity_id=a,
                relation=slugify(fix.right),
                dst_entity_id=b,
                evidence_item_id=evidence,
                title=f"{subject[0].name} {slugify(fix.right)} {obj[0].name}",
            )
        )
        return ops

    async def _bulk(self, ctx: CorrectContext, out: CorrectionOut) -> tuple[list[Op], list[str]]:
        words = (out.match or "").strip()
        slug = "/".join(slugify(p) for p in (out.category or "").split("/") if p.strip())
        if not words or not slug:
            return [], ["I couldn't tell what to re-file, or where to."]
        hits = await ctx.store.search(
            Query(words, None, ctx.steps.embedding_model), Filters(), Access(), limit=BULK_LIMIT
        )
        lexical = [h.item_id for h in hits if h.lexical]
        reader = ctx.memory.reader(ctx.scope)
        current = {c.id: c.slug for c in await reader.categories()}
        items = [
            i
            for i in await reader.items(lexical)
            if current.get(i.category_id) != slug  # type: ignore[arg-type]
        ]
        extra = [f"{len(items)} {'memory matches' if len(items) == 1 else 'memories match'}."]
        if out.rule_note:
            extra.append(
                "Doing that automatically from now on needs learned rules, which come later; "
                "for now I've re-filed what's there."
            )
        ops: list[Op] = [
            UpdateItem(item_id=i.id, changes={"category_slug": slug}, title=i.title) for i in items
        ]
        return ops, extra

    async def _offer_ops(self, ctx: CorrectContext, offer: Offer) -> list[Op]:
        items = await ctx.memory.reader(ctx.scope).items(list(offer.item_ids))
        ops: list[Op] = []
        for item in items:
            update: dict[str, Any] = {}
            attributes = dict(item.attributes)
            for key, value in offer.fix.items():
                if key.startswith("attributes."):
                    attributes[key.split(".", 1)[1]] = value
                else:
                    update[key] = Kind(value) if key == "kind" else value
            if attributes != item.attributes:
                update["attributes"] = attributes
            kind = update.get("kind", item.kind)
            if not valid_state(kind, item.state):
                update["state"] = "happened" if kind is Kind.EPISODE else initial_state(kind)
            if kind is Kind.EPISODE and item.occurred_start is None:
                # Logged on the day it was mentioned (stated in the reply).
                update |= {
                    "occurred_start": item.mentioned_at,
                    "time_precision": TimePrecision.DAY,
                }
            ops.append(UpdateItem(item_id=item.id, changes=update, title=item.title))
        return ops

    # ------------------------------------------------------------------ writing

    async def _commit(
        self,
        ctx: CorrectContext,
        ops: Sequence[Op],
        kind: str,
        notes: Sequence[str],
        *,
        prefix: str,
    ) -> CorrectOutcome:
        if not ops:
            return self._say(ctx, kind, " ".join(notes) or "Nothing needed changing.")
        turn = WriterTurn(
            turn_id=ctx.turn_id,
            workspace_id=ctx.scope.workspace_id,
            kind="edit" if ctx.origin == "ui_edit" else "user",
            now=ctx.now.instant,
        )
        async with ctx.trail.run(AgentStep.SAVE):
            writer = ctx.memory.writer(ctx.scope, turn, emit=ctx.trail.emit)
            writer.add(*(_origin(op, ctx.origin) for op in ops))
            commit = await writer.commit()
        if commit.touched_items:
            # Keys follow the correction at once (S2.14): a mistake's key says so.
            indexer = ctx.memory.keys(
                ctx.scope,
                timezone=ctx.now.timezone,
                embed=ctx.embed,
                model=ctx.steps.embedding_model,
            )
            await indexer.rebuild(sorted(commit.touched_items))
        text = summarise_commit(commit, prefix=prefix)
        if kind == "wrong_value" and any(e.op == "corrected" for e in commit.diff.entries):
            text += " The old version is kept as a mistake, left out of answers."
        reply = " ".join([text, *notes]).strip()
        async with ctx.trail.run(AgentStep.ANSWER):
            ctx.write(reply)
        return CorrectOutcome(reply=reply, kind=kind, commit=commit, notes=list(notes))

    @staticmethod
    def _say(ctx: CorrectContext, kind: str, text: str) -> CorrectOutcome:
        ctx.write(text)
        return CorrectOutcome(reply=text, kind=kind)


def _origin(op: Op, origin: str) -> Op:
    return op.model_copy(update={"origin": origin}) if origin != "user_message" else op


def _listing(prefix: str, items: Sequence[ItemRecord]) -> str:
    rows = [
        {
            "ref": f"{prefix}{n}",
            "kind": i.kind.value,
            "subtype": i.subtype,
            "state": i.state,
            "title": i.title,
            "text": i.text,
            "occurred": _iso(i.occurred_start),
            "due": _iso(i.due_at),
        }
        for n, i in enumerate(items, start=1)
    ]
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="minutes") if value else None

"""Reconciliation (S2.8, ADR-0019): not "is this a duplicate?" but "how does this relate to what
is already stored?". Each proposed memory gets one decision: new, add_detail, supersede,
fulfil, link or no_op, with the candidate and the score.

Deterministic rules settle the clear cases without a model call:

* same subject + predicate with a different value -> supersede (a same value is a no-op);
* an episode about something on the wish list -> fulfil that intention;
* episodes never merge; only the same time + same entities + same subtype is a duplicate;
* the same wish (same subtype, same work or thing) -> no-op, or add_detail if it adds some.

Only the fuzzy cases (similar keys, a plan that may have moved) go to the ``reconcile`` step.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from secondmind.core import (
    EntityKind,
    EntityRole,
    ItemStatus,
    Kind,
    ReconcileDecision,
    ReconcileInfo,
)
from secondmind.memory import ItemContent, ItemEntityRecord, ItemRecord, MemoryReader

THING_KINDS = frozenset(
    {EntityKind.WORK, EntityKind.THING, EntityKind.PLACE, EntityKind.TOPIC, EntityKind.PROJECT}
)


@dataclass(slots=True)
class Draft:
    """A proposed memory after time and entity resolution, before it is written."""

    ref: str
    item_id: uuid.UUID
    content: ItemContent
    entities: list[tuple[uuid.UUID, EntityRole, EntityKind]] = field(default_factory=list)

    @property
    def things(self) -> list[uuid.UUID]:
        """The works, things, places and topics it is about (what makes two items the same)."""
        return [
            e
            for e, role, kind in self.entities
            if kind in THING_KINDS and role in (EntityRole.ABOUT, EntityRole.AT, EntityRole.PART_OF)
        ]


@dataclass(frozen=True, slots=True)
class Decision:
    decision: ReconcileDecision
    candidate: ItemRecord | None = None
    score: float | None = None
    rule: str = ""

    @property
    def info(self) -> ReconcileInfo:
        return ReconcileInfo(
            decision=self.decision,
            candidate_id=self.candidate.id if self.candidate else None,
            candidate_title=self.candidate.title if self.candidate else None,
            score=None if self.score is None else round(self.score, 3),
            rule=self.rule,
        )


# (draft, candidates with scores) -> (decision, index of the chosen candidate or None, reason)
Chooser = Callable[
    [Draft, Sequence[tuple[ItemRecord, float | None]]],
    Awaitable[tuple[ReconcileDecision, int | None, str]],
]
EmbedOne = Callable[[str], Awaitable[list[float] | None]]


class Reconciler:
    def __init__(
        self,
        reader: MemoryReader,
        *,
        threshold: float,
        embed: EmbedOne | None,
        model: str,
        choose: Chooser | None,
    ) -> None:
        self._reader = reader
        self._threshold = threshold
        self._embed = embed
        self._model = model
        self._choose = choose

    async def decide(self, draft: Draft) -> Decision:  # noqa: PLR0911
        content = draft.content
        if (
            content.kind in (Kind.FACT, Kind.PREFERENCE)
            and content.subject_entity_id
            and content.predicate
        ):
            current = await self._reader.find_items(
                kinds=[content.kind],
                subject_entity_id=content.subject_entity_id,
                predicate=content.predicate,
                current_only=True,
            )
            current = [c for c in current if c.id != draft.item_id]
            if current:
                old = current[0]
                if same_value(old, content):
                    return Decision(
                        ReconcileDecision.NO_OP, old, 1.0, "same subject + predicate, same value"
                    )
                return Decision(
                    ReconcileDecision.SUPERSEDE, old, 1.0, "same subject + predicate, new value"
                )
        if content.kind is Kind.EPISODE:
            return await self._episode(draft)
        if content.kind is Kind.INTENTION:
            same = await self._same_wish(draft)
            if same is not None:
                if adds_detail(same, draft):
                    return Decision(
                        ReconcileDecision.ADD_DETAIL, same, 1.0, "same wish, with more detail"
                    )
                return Decision(ReconcileDecision.NO_OP, same, 1.0, "same wish saved again")
        if content.kind is Kind.PLAN and draft.entities:
            plans = await self._related_plans(draft)
            if plans:
                return await self._ask(draft, [(p, None) for p in plans], "plan for the same thing")
        return await self._similar(draft)

    async def _episode(self, draft: Draft) -> Decision:
        content = draft.content
        for thing in draft.things:
            wishes = await self._reader.find_items(
                kinds=[Kind.INTENTION], states=["wanted", "active"], entity_id=thing
            )
            if wishes:
                return Decision(
                    ReconcileDecision.FULFIL,
                    wishes[0],
                    1.0,
                    "episode about something on the wish list",
                )
        if content.occurred_start is not None:
            anchor = draft.things[0] if draft.things else None
            episodes = await self._reader.find_items(
                kinds=[Kind.EPISODE], entity_id=anchor, limit=50
            )
            mine = {e for e, _, _ in draft.entities}
            links = await self._reader.item_entities([e.id for e in episodes])
            for episode in episodes:
                theirs = {r.entity_id for r in links if r.item_id == episode.id}
                if (
                    episode.occurred_start == content.occurred_start
                    and episode.time_precision == content.time_precision
                    and episode.subtype == content.subtype
                    and theirs == mine
                ):
                    return Decision(
                        ReconcileDecision.NO_OP, episode, 1.0, "same time, entities and subtype"
                    )
        return Decision(ReconcileDecision.NEW, rule="episodes never merge")

    async def _same_wish(self, draft: Draft) -> ItemRecord | None:
        for thing in draft.things:
            wishes = await self._reader.find_items(
                kinds=[Kind.INTENTION], states=["wanted", "active"], entity_id=thing
            )
            for wish in wishes:
                if wish.subtype == draft.content.subtype and wish.id != draft.item_id:
                    return wish
        return None

    async def _related_plans(self, draft: Draft) -> list[ItemRecord]:
        out: dict[uuid.UUID, ItemRecord] = {}
        for entity_id, _, kind in draft.entities:
            if kind is EntityKind.SELF:
                continue
            for plan in await self._reader.find_items(
                kinds=[Kind.PLAN], states=["scheduled"], entity_id=entity_id
            ):
                if plan.subtype == draft.content.subtype and plan.id != draft.item_id:
                    out[plan.id] = plan
        return list(out.values())[:5]

    async def _similar(self, draft: Draft) -> Decision:
        if self._embed is None:
            return Decision(ReconcileDecision.NEW, rule="no similar memories")
        vector = await self._embed(draft.content.text)
        if vector is None:
            return Decision(ReconcileDecision.NEW, rule="similarity unavailable")
        found = await self._reader.similar_items(vector, limit=5, model=self._model)
        close = [(item_id, score) for item_id, score in found if score >= self._threshold]
        if not close:
            return Decision(ReconcileDecision.NEW, rule="nothing similar above the threshold")
        items = {i.id: i for i in await self._reader.items([i for i, _ in close])}
        links = await self._reader.item_entities(list(items))
        mine = set(draft.things)
        candidates: list[tuple[ItemRecord, float | None]] = []
        for item_id, score in close:
            item = items.get(item_id)
            if item is None or item.status is not ItemStatus.ACTIVE or item.id == draft.item_id:
                continue
            if item.kind is Kind.EPISODE or draft.content.kind is Kind.EPISODE:
                continue  # episodes never merge
            theirs = _thing_ids(links, item_id)
            if mine and theirs and not (mine & theirs):
                continue  # two different shows with similar descriptions are two items
            candidates.append((item, score))
        if not candidates:
            return Decision(ReconcileDecision.NEW, rule="similar items are about different things")
        return await self._ask(draft, candidates, "similar keys")

    async def _ask(
        self, draft: Draft, candidates: Sequence[tuple[ItemRecord, float | None]], why: str
    ) -> Decision:
        if self._choose is None:
            return Decision(ReconcileDecision.NEW, rule=f"{why}; no model to decide")
        decision, index, reason = await self._choose(draft, candidates)
        if decision is ReconcileDecision.NEW or index is None or not 0 <= index < len(candidates):
            return Decision(ReconcileDecision.NEW, rule=f"{why}; model: {reason}")
        item, score = candidates[index]
        return Decision(decision, item, score, f"{why}; model: {reason}")


def _thing_ids(links: Sequence[ItemEntityRecord], item_id: uuid.UUID) -> set[uuid.UUID]:
    roles = (EntityRole.ABOUT, EntityRole.AT, EntityRole.PART_OF)
    return {r.entity_id for r in links if r.item_id == item_id and r.role in roles}


def same_value(old: ItemRecord, new: ItemContent) -> bool:
    if old.value and new.value:
        return _value_key(old.value) == _value_key(new.value)
    return " ".join(old.text.lower().split()) == " ".join(new.text.lower().split())


def _value_key(value: dict[str, object]) -> tuple[str, str]:
    text = str(value.get("text") or "").strip().lower()
    number = value.get("number")
    unit = str(value.get("unit") or "").strip().lower()
    return (text or str(number), unit)


def adds_detail(existing: ItemRecord, draft: Draft) -> bool:
    new = draft.content
    return bool(
        set(new.tags) - set(existing.tags)
        or {k: v for k, v in new.attributes.items() if existing.attributes.get(k) != v}
        or (new.summary and not existing.summary)
    )

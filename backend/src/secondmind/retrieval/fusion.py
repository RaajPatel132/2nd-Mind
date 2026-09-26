"""Fusion (S3.6): every channel (each tool's result list and the soft channel) fused by
reciprocal rank fusion, ``sum(1 / (k + rank))``. Keys were already collapsed to items inside
each channel, so one memory takes one slot. Each candidate records which channels found it
and its rank in each; when channels agree the item rises, and a soft-only hit still makes the
list.

**History is demoted, not hidden**: superseded, moved, cancelled and dropped rows stay, with
their score multiplied by ``HISTORY_DEMOTION``, except for the ``history`` and ``why`` shapes,
where old rows are the point.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from secondmind.core import FoundBy, KeyKind, RetrievalCandidate, Shape
from secondmind.memory import ItemRecord
from secondmind.retrieval.tools import Hit

SOFT = "soft"
HISTORY_STATES = frozenset({"superseded", "moved", "cancelled", "dropped"})
NO_DEMOTION = frozenset({Shape.HISTORY, Shape.WHY})


def is_history(item: ItemRecord) -> bool:
    return item.state in HISTORY_STATES or item.valid_to is not None


@dataclass(slots=True)
class Candidate:
    item_id: uuid.UUID
    found_by: dict[str, int] = field(default_factory=dict)
    lexical: float | None = None
    dense: float | None = None
    matched_key: KeyKind | None = None
    fused: float = 0.0
    demoted: bool = False
    rerank_score: float | None = None
    rerank_reason: str = ""
    selected: bool = False
    cited: bool = False
    reason: str = ""

    @property
    def soft_only(self) -> bool:
        return set(self.found_by) == {SOFT}

    @property
    def filtered(self) -> bool:
        """Found by at least one filtered tool (not only by the soft channel)."""
        return any(channel != SOFT for channel in self.found_by)

    def trace(self, item: ItemRecord | None) -> RetrievalCandidate:
        return RetrievalCandidate(
            item_id=self.item_id,
            title=item.title if item else "(gone)",
            kind=item.kind if item else None,
            state=item.state if item else None,
            layer=item.layer if item else RetrievalCandidate.model_fields["layer"].default,
            found_by=[
                FoundBy(channel=c, rank=r)
                for c, r in sorted(self.found_by.items(), key=lambda kv: kv[1])
            ],
            matched_key=self.matched_key,
            lexical_score=_round(self.lexical),
            dense_score=_round(self.dense),
            fused_score=_round(self.fused),
            rerank_score=_round(self.rerank_score),
            rerank_reason=self.rerank_reason,
            soft_only=self.soft_only,
            demoted=self.demoted,
            selected=self.selected,
            cited=self.cited,
            reason=self.reason,
        )


def fuse(
    channels: Mapping[str, Sequence[Hit]],
    items: Mapping[uuid.UUID, ItemRecord],
    *,
    shape: Shape,
    rrf_k: int,
    history_demotion: float,
) -> list[Candidate]:
    """Fuse the channels of one sub-query into candidates, best first."""
    by_id: dict[uuid.UUID, Candidate] = {}
    for channel, hits in channels.items():
        for hit in hits:
            if hit.item_id not in items:
                continue
            cand = by_id.setdefault(hit.item_id, Candidate(item_id=hit.item_id))
            rank = cand.found_by.get(channel)
            if rank is None or hit.rank < rank:
                cand.found_by[channel] = hit.rank
            cand.fused += 1.0 / (rrf_k + hit.rank)
            if hit.lexical is not None:
                cand.lexical = max(cand.lexical or 0.0, hit.lexical)
            if hit.dense is not None:
                cand.dense = max(cand.dense or -1.0, hit.dense)
            if hit.matched_key is not None and (cand.matched_key is None or channel == SOFT):
                cand.matched_key = hit.matched_key
    for cand in by_id.values():
        if shape not in NO_DEMOTION and is_history(items[cand.item_id]):
            cand.fused *= history_demotion
            cand.demoted = True
    return sorted(
        by_id.values(),
        key=lambda c: (-c.fused, -_when(items[c.item_id]), str(c.item_id)),
    )


def _when(item: ItemRecord) -> float:
    moment = item.occurred_start or item.valid_from or item.due_at or item.mentioned_at
    return moment.timestamp()


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)

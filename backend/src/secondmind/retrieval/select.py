"""Rerank and selection (S3.7), and the count cross-check (S3.6).

``rerank@1`` scores the top ``RERANK_TOP_N`` fused candidates of a sub-query listwise, from
their verbalised key, kind, state and dates, with a one-line reason each. Selection then:

* ``list``, ``set``, ``time_window`` and ``situational`` (and code's guaranteed expansions): all
  that the filtered tools returned goes through, up to ``LIST_MAX_ITEMS`` ("and 12 more"
  beyond); rerank only decides which soft-only extras join;
* every other shape: the top ``ANSWER_TOP_K`` scoring at least ``RERANK_MIN_SCORE``;
* ``count``: the items the aggregate counted are always cited (the number is exact whatever
  they score).

Without rerank (switched off, or it failed) the fused order is used with a floor: a candidate
must have been found by a filtered tool, or share a word with the question.
"""

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel

from secondmind.config import Step
from secondmind.core import CountCheck, Kind, Shape
from secondmind.ingestion import ModelSteps
from secondmind.memory import ItemRecord, format_when
from secondmind.providers import ChatMessage
from secondmind.retrieval.fusion import SOFT, Candidate
from secondmind.retrieval.plan import SubQuery
from secondmind.retrieval.schemas import RerankOut
from secondmind.retrieval.tools import Filters

LIST_LIKE = frozenset({Shape.LIST, Shape.SET, Shape.TIME_WINDOW, Shape.SITUATIONAL})


@dataclass(frozen=True, slots=True)
class SelectSettings:
    rerank_min_score: float = 0.5
    answer_top_k: int = 8
    list_max_items: int = 20
    count_check_min_score: float = 0.6


class RerankVars(BaseModel):
    now: str
    timezone: str


@dataclass(slots=True)
class Selection:
    selected: list[Candidate]
    more: int = 0
    note: str = ""


async def rerank(
    steps: ModelSteps,
    question: str,
    candidates: Sequence[Candidate],
    items: Mapping[uuid.UUID, ItemRecord],
    keys: Mapping[uuid.UUID, str],
    *,
    now: datetime,
    timezone: str,
) -> None:
    """Score ``candidates`` in place (``rerank_score`` and ``rerank_reason``). Raises
    ``ProviderUnavailableError`` if the step can't be served; unknown ids are ignored."""
    if not candidates:
        return
    listing = [
        {
            "id": f"c{n}",
            "kind": items[c.item_id].kind.value,
            "state": items[c.item_id].state,
            "dates": _dates(items[c.item_id], timezone),
            "text": keys.get(c.item_id) or items[c.item_id].text,
        }
        for n, c in enumerate(candidates, start=1)
    ]
    out = await steps.structured(
        Step.RERANK,
        RerankOut,
        RerankVars(now=now.astimezone().isoformat(timespec="minutes"), timezone=timezone),
        [ChatMessage.user(json.dumps({"question": question, "candidates": listing}))],
    )
    by_label = {f"c{n}": c for n, c in enumerate(candidates, start=1)}
    for score in out.scores:
        cand = by_label.get(score.id.strip().lower())
        if cand is not None:
            cand.rerank_score = min(1.0, max(0.0, float(score.score)))
            cand.rerank_reason = " ".join(score.reason.split())[:200]


def select(
    sub: SubQuery,
    candidates: Sequence[Candidate],
    *,
    reranked: bool,
    settings: SelectSettings,
    counted: Sequence[uuid.UUID] = (),
) -> Selection:
    """Decide what reaches the answer; marks ``selected`` and ``reason`` on every candidate."""
    if sub.shape is Shape.COUNT:
        chosen = [c for c in candidates if c.item_id in set(counted)]
        chosen.sort(key=lambda c: list(counted).index(c.item_id))
        for c in candidates:
            c.selected = c in chosen
            c.reason = "counted, so cited" if c.selected else "not counted"
        return Selection(selected=chosen)

    def extra_ok(c: Candidate) -> bool:
        if reranked and c.rerank_score is not None:
            return c.rerank_score >= settings.rerank_min_score
        return bool(c.lexical)

    if sub.shape in LIST_LIKE or (sub.expansion and sub.expansion.source == "code"):
        base = [c for c in candidates if c.filtered]
        # A set is an exact operation: something similar that failed it isn't an extra.
        extras = [
            c for c in candidates if c.soft_only and extra_ok(c) and sub.shape is not Shape.SET
        ]
        kept = base[: settings.list_max_items]
        more = max(0, len(base) - len(kept))
        chosen = kept + extras[: settings.answer_top_k]
        for c in candidates:
            c.selected = c in chosen
            if c in kept:
                c.reason = "found by " + ", ".join(sorted(c.found_by))
            elif c in chosen:
                c.reason = _score_reason(c, reranked, "soft-only extra")
            elif c in base:
                c.reason = f"over the list limit of {settings.list_max_items}"
            else:
                c.reason = _score_reason(c, reranked, "below the bar")
        return Selection(selected=chosen, more=more)

    pool = list(candidates)
    if reranked:
        pool.sort(key=lambda c: -(c.rerank_score if c.rerank_score is not None else -1.0))
        chosen = [
            c
            for c in pool
            if c.rerank_score is not None and c.rerank_score >= settings.rerank_min_score
        ][: settings.answer_top_k]
    else:
        chosen = [c for c in pool if c.filtered or c.lexical][: settings.answer_top_k]
    for c in candidates:
        c.selected = c in chosen
        c.reason = _score_reason(c, reranked, "selected" if c.selected else "not selected")
    return Selection(selected=chosen)


def _score_reason(c: Candidate, reranked: bool, lead: str) -> str:
    if reranked and c.rerank_score is not None:
        return f"{lead}: rerank {c.rerank_score:.2f}"
    return f"{lead}: fused {c.fused:.3f}" + ("" if c.lexical else ", no shared words")


def count_check(
    sub: SubQuery,
    candidates: Sequence[Candidate],
    items: Mapping[uuid.UUID, ItemRecord],
    counted: Sequence[uuid.UUID],
    *,
    min_score: float,
) -> CountCheck | None:
    """Soft-channel hits in the count's window that look like what was counted but weren't:
    "6 logged runs; 2 more mentions look like runs but weren't logged as runs.\""""
    counted_set = set(counted)
    window = sub.window
    extras: list[uuid.UUID] = []
    for c in candidates:
        if c.item_id in counted_set or SOFT not in c.found_by:
            continue
        item = items[c.item_id]
        when = item.occurred_start or item.mentioned_at
        if window is not None and not window.contains(when):
            continue
        score = c.rerank_score if c.rerank_score is not None else (c.dense or 0.0)
        if score >= min_score:
            extras.append(c.item_id)
    if not extras:
        return None
    one, many = count_label(sub.filters)
    n = len(extras)
    mentions = "1 more mention looks" if n == 1 else f"{n} more mentions look"
    return CountCheck(
        label=many,
        extra_ids=extras,
        note=f"{mentions} like {many} but weren't logged as {many}.",
        offer=f"File {'it as a ' + one if n == 1 else 'them as ' + many}?",
        fix=_fix(sub.filters),
    )


def count_label(filters: Filters) -> tuple[str, str]:
    """What was counted, singular and plural: "run"/"runs", "measurement"/"measurements"."""
    word = None
    if filters.attribute:
        word = filters.attribute[1].strip().lower()
    elif filters.subtypes and filters.subtypes[0] not in ("measurement", "experience"):
        word = filters.subtypes[0].replace("_", " ")
    elif filters.kinds:
        word = filters.kinds[0].value
    word = word or "item"
    word = word.removesuffix("s") if len(word) > 3 and word.endswith("s") else word
    return word, word + ("es" if word.endswith(("s", "x", "ch", "sh")) else "s")


def _fix(filters: Filters) -> dict[str, str]:
    fix: dict[str, str] = {}
    if filters.kinds:
        fix["kind"] = filters.kinds[0].value
    if filters.subtypes:
        fix["subtype"] = filters.subtypes[0]
    if filters.attribute:
        fix[f"attributes.{filters.attribute[0]}"] = filters.attribute[1]
    if not fix:
        fix["kind"] = Kind.EPISODE.value
    return fix


def _dates(item: ItemRecord, tz: str) -> str:
    bits: list[str] = []
    if item.occurred_start:
        bits.append("occurred " + format_when(item.occurred_start, item.time_precision, tz))
    if item.due_at:
        bits.append("due " + format_when(item.due_at, item.time_precision, tz))
    if item.valid_from:
        bits.append("true from " + format_when(item.valid_from, item.time_precision, tz))
    if item.valid_to:
        bits.append("until " + format_when(item.valid_to, None, tz))
    bits.append("mentioned " + format_when(item.mentioned_at, None, tz))
    return "; ".join(bits)

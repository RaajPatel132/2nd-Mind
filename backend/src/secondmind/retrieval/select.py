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
from zoneinfo import ZoneInfo

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
    passage_floor: float = 0.0,
) -> None:
    """Score ``candidates`` in place (``rerank_score`` and ``rerank_reason``). Raises
    ``ProviderUnavailableError`` if the step can't be served; unknown ids are ignored.

    The reranker sees each memory's own text and never a page's passages (what a page says reaches
    a model only at save and in the answer, ADR-0037), so a memory found by what its page says is
    judged on its summary alone. ``passage_floor`` keeps such a memory in play: see
    :func:`keep_passage_matches`."""
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
        RerankVars(now=local_now(now, timezone), timezone=timezone),
        [ChatMessage.user(json.dumps({"question": question, "candidates": listing}))],
    )
    by_label = {f"c{n}": c for n, c in enumerate(candidates, start=1)}
    for score in out.scores:
        cand = by_label.get(score.id.strip().lower())
        if cand is not None:
            cand.rerank_score = min(1.0, max(0.0, float(score.score)))
            cand.rerank_reason = " ".join(score.reason.split())[:200]
    keep_passage_matches(candidates, passage_floor)


PASSAGE_KEPT = "kept: a passage of the page matches your words (the reranker saw only its summary)"


def keep_passage_matches(candidates: Sequence[Candidate], floor: float) -> None:
    """Lift a memory whose saved page matched the question's words up to ``floor``, when the
    reranker scored it lower. It could not see the passage, so a low score from the summary alone
    is no evidence against it; the answer step reads the passage and says so if it doesn't help."""
    for cand in candidates:
        if cand.snippet is None or not cand.lexical:
            continue
        if cand.rerank_score is None or cand.rerank_score < floor:
            cand.rerank_score = floor
            cand.rerank_reason = PASSAGE_KEPT


def select(
    sub: SubQuery,
    candidates: Sequence[Candidate],
    *,
    reranked: bool,
    settings: SelectSettings,
    counted: Sequence[uuid.UUID] = (),
    items: Mapping[uuid.UUID, ItemRecord] | None = None,
) -> Selection:
    """Decide what reaches the answer; marks ``selected`` and ``reason`` on every candidate."""
    if sub.missing_entity:
        # "Nisha's husband" when she has none: nothing found by meaning answers for them.
        for c in candidates:
            c.selected, c.reason = False, "the person or thing asked about isn't known"
        return Selection(selected=[])
    if sub.shape is Shape.COUNT:
        chosen = [c for c in candidates if c.item_id in set(counted)]
        chosen.sort(key=lambda c: list(counted).index(c.item_id))
        for c in candidates:
            c.selected = c in chosen
            c.reason = "counted, so cited" if c.selected else "not counted"
        return Selection(selected=chosen)
    if sub.shape in LIST_LIKE or (sub.expansion and sub.expansion.source == "code"):
        return _select_list(sub, candidates, reranked=reranked, settings=settings, items=items)
    return _select_ranked(candidates, reranked=reranked, settings=settings)


def _passes(c: Candidate, reranked: bool, settings: SelectSettings) -> bool:
    if reranked and c.rerank_score is not None:
        return c.rerank_score >= settings.rerank_min_score
    return bool(c.lexical)


def _in_window(sub: SubQuery, item: ItemRecord) -> bool:
    """Whether a memory belongs to the question's period by when it happened or was said. A
    purchase made in August and mentioned in September is a "filter miss" that still belongs
    to September's answers; something that happened and was said after the period doesn't."""
    window = sub.window
    if window is None:
        return True
    moments = [d for d in (item.occurred_start, item.due_at, item.mentioned_at) if d is not None]
    return any(window.contains(moment) for moment in moments)


def _select_list(
    sub: SubQuery,
    candidates: Sequence[Candidate],
    *,
    reranked: bool,
    settings: SelectSettings,
    items: Mapping[uuid.UUID, ItemRecord] | None = None,
) -> Selection:
    base = _current_first([c for c in candidates if c.filtered])

    def joins(c: Candidate) -> bool:
        if not (c.soft_only and _passes(c, reranked, settings)):
            return False
        # A set is an exact operation: something similar that failed it isn't an extra.
        if sub.shape is Shape.SET:
            return False
        item = items.get(c.item_id) if items is not None else None
        return not (
            sub.shape is Shape.TIME_WINDOW and item is not None and not _in_window(sub, item)
        )

    extras = [c for c in candidates if joins(c)]
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
        elif c.demoted and c.filtered:
            c.reason = EARLIER
        else:
            c.reason = _score_reason(c, reranked, "below the bar")
    return Selection(selected=chosen, more=more)


def _select_ranked(
    candidates: Sequence[Candidate], *, reranked: bool, settings: SelectSettings
) -> Selection:
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
    chosen = _current_first(chosen)
    for c in candidates:
        c.selected = c in chosen
        if not c.selected and c.demoted and reranked and _passes(c, reranked, settings):
            c.reason = EARLIER
        else:
            c.reason = _score_reason(c, reranked, "selected" if c.selected else "not selected")
    return Selection(selected=chosen)


EARLIER = "an earlier value: a current one is cited"


def _current_first(chosen: list[Candidate]) -> list[Candidate]:
    """Rows that no longer hold (superseded, moved, cancelled, dropped: ``demoted``, outside
    history and why questions) are cited only when nothing current answers. They stay in the
    trace (found in R.3: a live reranker scored "I live in Bengaluru" as highly as Pune)."""
    current = [c for c in chosen if not c.demoted]
    return current if current else chosen


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


def local_now(now: datetime, timezone: str) -> str:
    """``now`` as the workspace's wall clock (never the server's) for a prompt."""
    return now.astimezone(ZoneInfo(timezone)).isoformat(timespec="minutes")

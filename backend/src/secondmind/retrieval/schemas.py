"""Structured outputs of the recall steps: the query plan, the rerank scores (S3.4, S3.7).

As in ingestion, every field is required and optional ones are nullable (no defaults), so the
schemas work with both providers' strict modes. **The plan has no field for a date**: time
expressions come back verbatim and code resolves them (``resolve_window``), so the model can't
supply a date it worked out. Code validates the meaning on top (``validate_plan``).
"""

import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

ShapeLabel = Literal[
    "exact", "list", "latest", "history", "time_window", "order", "count", "set", "entity",
    "semantic", "why", "situational", "conversation",
]  # fmt: skip
KindLabel = Literal[
    "fact", "preference", "episode", "plan", "task", "intention", "resource", "note", "rule",
    "pattern",
]  # fmt: skip
RoleLabel = Literal["about", "with", "for", "by", "at", "owner", "part_of"]
PlanClock = Literal["occurred", "valid", "due", "mentioned"]
PlanDirection = Literal["past", "future", "any"]


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanTime(_Out):
    expression: str
    clock: PlanClock
    direction: PlanDirection
    anchor: str | None


class PlanEntity(_Out):
    mention: str
    name: str | None
    path: list[str]


class PlanAttribute(_Out):
    key: str
    value: str


class PlanFilters(_Out):
    kinds: list[KindLabel]
    subtypes: list[str]
    states: list[str]
    category: str | None
    predicate: str | None
    attribute: PlanAttribute | None
    role: RoleLabel | None
    current_only: bool


class PlanAggregate(_Out):
    op: Literal["count", "sum", "min", "max", "average"]
    field: str | None
    group_by: Literal["day", "week", "month", "subtype"] | None


class PlanSet(_Out):
    op: Literal["without", "shared_with"]
    other_kind: KindLabel | None
    entity: str | None


class SubQueryOut(_Out):
    shape: ShapeLabel
    question: str
    topic: str
    about: str | None
    times: list[PlanTime]
    entities: list[PlanEntity]
    filters: PlanFilters
    aggregate: PlanAggregate | None
    set: PlanSet | None
    role: Literal["user", "assistant"] | None
    about_sensitive: bool
    expanded_from: str | None


class QueryPlanOut(_Out):
    sub_queries: list[SubQueryOut]


class RerankScore(_Out):
    id: str
    score: float
    reason: str


class RerankOut(_Out):
    scores: list[RerankScore]


MAX_SUB_QUERIES = 6
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_\- /]*$")


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def validate_plan(out: QueryPlanOut, said: Sequence[str]) -> list[str]:
    """Meaning-level checks. ``said`` is the question and the recent turns: every time
    expression and anchor must be copied from them word for word (never a worked-out date)."""
    errors: list[str] = []
    if not out.sub_queries:
        errors.append("a plan needs at least one sub-query")
    if len(out.sub_queries) > MAX_SUB_QUERIES:
        errors.append(f"at most {MAX_SUB_QUERIES} sub-queries")
    heard = " ".join(_norm(s) for s in said)
    for n, sq in enumerate(out.sub_queries, start=1):
        errors.extend(_check(f"sub-query {n}", sq, heard))
    return errors


def _check(where: str, sq: SubQueryOut, heard: str) -> list[str]:
    errors: list[str] = []
    if not sq.question.strip() or not sq.topic.strip():
        errors.append(f"{where}: question and topic are required")
    if sq.shape == "count" and sq.aggregate is None:
        errors.append(f"{where}: a count needs an aggregate")
    if sq.aggregate is not None and sq.aggregate.op != "count" and not sq.aggregate.field:
        errors.append(f"{where}: {sq.aggregate.op} needs a field")
    if sq.shape == "set" and sq.set is None:
        errors.append(f"{where}: a set question needs a set operation")
    if sq.set is not None and sq.set.op == "shared_with" and not sq.set.entity:
        errors.append(f"{where}: shared_with needs an entity")
    for t in sq.times:
        if not t.expression.strip():
            errors.append(f"{where}: a time needs its expression, even with an anchor")
        elif _norm(t.expression) not in heard:
            errors.append(
                f"{where}: time expression {t.expression!r} is not in the question; copy the "
                "words used, never a date you worked out"
            )
    errors.extend(
        f"{where}: relation paths are at most 2 hops" for e in sq.entities if len(e.path) > 2
    )
    slugs = [*sq.filters.subtypes, *(p for e in sq.entities for p in e.path)]
    errors.extend(f"{where}: {s!r} is not a slug" for s in slugs if not _SLUG.match(s.lower()))
    return errors

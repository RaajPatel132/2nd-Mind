"""The retrieval tools (S3.5): typed arguments and results, and the ``RecallStore`` port.

Each tool is one read-only SQL query in ``retrieval.adapters`` run with the turn's workspace
scope. Every tool applies the **mandatory filters** itself: ``status = active``, never
``secret``, and ``sensitive`` only when the question is about it (``Access``). Rows corrected as
mistakes are archived, so ``status = active`` leaves them out too.

Tools return item ids in rank order plus their scores; the pipeline hydrates the rows.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal, Protocol

from secondmind.core import EntityRole, KeyKind, Kind, TimeClock

ToolName = Literal[
    "lookup", "aggregate", "search", "entity", "timeline", "history", "conversation", "soft"
]
AggregateOp = Literal["count", "sum", "min", "max", "average"]
GroupBy = Literal["day", "week", "month", "subtype"]


@dataclass(frozen=True, slots=True)
class Access:
    """The mandatory part of every query: may ``sensitive`` items be returned?"""

    sensitive: bool = False


@dataclass(frozen=True, slots=True)
class WindowFilter:
    clock: TimeClock
    start: datetime | None
    end: datetime | None


@dataclass(frozen=True, slots=True)
class SetOp:
    """``without``: drop items whose object (a place, a work) already has an item of
    ``other_kind`` (a place I want to try with no episode at it). ``shared_with``: keep items
    whose object is also wanted with ``entity_id`` (shows Kabir and I both want to watch)."""

    op: Literal["without", "shared_with"]
    other_kind: Kind | None = None
    entity_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class Filters:
    """Structured filters from the resolved plan. Empty means "not filtered on"."""

    kinds: tuple[Kind, ...] = ()
    subtypes: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    category: str | None = None
    entity_ids: tuple[uuid.UUID, ...] = ()
    roles: tuple[EntityRole, ...] = ()
    predicate: str | None = None
    attribute: tuple[str, str] | None = None
    window: WindowFilter | None = None
    current_only: bool = False

    def without(self, **changes: object) -> "Filters":
        return replace(self, **changes)  # type: ignore[arg-type]

    @property
    def empty(self) -> bool:
        return self == Filters()

    def describe(self) -> dict[str, str]:
        """Short strings for the glass box."""
        out: dict[str, str] = {}
        if self.kinds:
            out["kind"] = ",".join(k.value for k in self.kinds)
        if self.subtypes:
            out["subtype"] = ",".join(self.subtypes)
        if self.states:
            out["state"] = ",".join(self.states)
        if self.category:
            out["category"] = self.category
        if self.entity_ids:
            out["entities"] = str(len(self.entity_ids))
        if self.roles:
            out["role"] = ",".join(r.value for r in self.roles)
        if self.predicate:
            out["predicate"] = self.predicate
        if self.attribute:
            out["attribute"] = f"{self.attribute[0]}={self.attribute[1]}"
        if self.window:
            start = self.window.start.isoformat(timespec="minutes") if self.window.start else "…"
            end = self.window.end.isoformat(timespec="minutes") if self.window.end else "…"
            out["window"] = f"{self.window.clock.value} [{start}, {end})"
        if self.current_only:
            out["current"] = "yes"
        return out


@dataclass(frozen=True, slots=True)
class Hit:
    """One item a channel found, in rank order (1 = best)."""

    item_id: uuid.UUID
    rank: int
    lexical: float | None = None
    dense: float | None = None
    matched_key: KeyKind | None = None


@dataclass(frozen=True, slots=True)
class LookupResult:
    hits: list[Hit]
    total: int


@dataclass(frozen=True, slots=True)
class Group:
    key: str
    value: float
    count: int


@dataclass(frozen=True, slots=True)
class AggregateResult:
    op: AggregateOp
    value: float | None
    item_ids: list[uuid.UUID]
    groups: list[Group] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PathHop:
    """One relation hop: the target is ``relation`` of the entity before it ("Nisha's
    husband": the target is ``spouse_of`` Nisha)."""

    relation: str


@dataclass(frozen=True, slots=True)
class EntityResult:
    """The entities reached (the base ones, or the end of a relation path) and the path taken,
    with their linked items, current state first."""

    entity_ids: list[uuid.UUID]
    paths: list[list[str]]
    hits: list[Hit]


@dataclass(frozen=True, slots=True)
class Occurrence:
    """When a timeline item falls in the window: a dated item, one occurrence of a routine,
    a task's due date or a pending reminder."""

    item_id: uuid.UUID
    start: datetime
    end: datetime | None
    via: Literal["occurred", "due", "valid", "mentioned", "trigger", "routine"]
    upcoming: bool


@dataclass(frozen=True, slots=True)
class TimelineResult:
    occurrences: list[Occurrence]

    @property
    def hits(self) -> list[Hit]:
        """One hit per item, at its first occurrence."""
        seen: dict[uuid.UUID, int] = {}
        for o in self.occurrences:
            seen.setdefault(o.item_id, len(seen) + 1)
        return [Hit(item_id=i, rank=r) for i, r in seen.items()]


@dataclass(frozen=True, slots=True)
class HistoryRow:
    item_id: uuid.UUID
    valid_from: datetime | None
    valid_to: datetime | None
    state: str
    change_key: str | None = None


@dataclass(frozen=True, slots=True)
class HistoryResult:
    """Oldest to newest, with the ``change`` keys and the items linked by ``because``."""

    rows: list[HistoryRow]
    because: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list)

    @property
    def hits(self) -> list[Hit]:
        ids = [r.item_id for r in self.rows] + [b for _, b in self.because]
        return [Hit(item_id=i, rank=n + 1) for n, i in enumerate(dict.fromkeys(ids))]


@dataclass(frozen=True, slots=True)
class ConversationHit:
    turn_id: uuid.UUID
    role: Literal["user", "assistant"]
    seq: int
    text: str
    said_at: datetime
    rank: int
    lexical: float | None = None
    dense: float | None = None


@dataclass(frozen=True, slots=True)
class Query:
    """The text side of a hybrid search: words for the lexical half, a vector for the dense."""

    text: str
    vector: Sequence[float] | None
    model: str


class RecallStore(Protocol):
    """The seven tools, bound to one workspace. Implementations never write."""

    async def lookup(
        self,
        filters: Filters,
        access: Access,
        *,
        set_op: SetOp | None = None,
        limit: int = 50,
    ) -> LookupResult: ...

    async def aggregate(
        self,
        filters: Filters,
        access: Access,
        *,
        op: AggregateOp,
        field: str | None = None,
        group_by: GroupBy | None = None,
        timezone: str = "UTC",
    ) -> AggregateResult: ...

    async def search(
        self, query: Query, filters: Filters, access: Access, *, limit: int
    ) -> list[Hit]:
        """Hybrid (lexical + dense, fused by RRF) over ``memory_keys``, keys collapsed to
        items. With empty filters this is the soft channel."""
        ...

    async def entity(
        self,
        entity_ids: Sequence[uuid.UUID],
        access: Access,
        *,
        path: Sequence[PathHop] = (),
        limit: int = 50,
    ) -> EntityResult: ...

    async def timeline(
        self,
        window: WindowFilter,
        filters: Filters,
        access: Access,
        *,
        now: datetime,
        timezone: str,
        limit: int = 100,
    ) -> TimelineResult: ...

    async def history(
        self,
        access: Access,
        *,
        subject_entity_id: uuid.UUID | None = None,
        predicate: str | None = None,
        item_ids: Sequence[uuid.UUID] = (),
    ) -> HistoryResult: ...

    async def conversation(
        self,
        query: Query,
        *,
        role: Literal["user", "assistant"] | None,
        window: WindowFilter | None,
        exclude_turn: uuid.UUID | None,
        limit: int = 10,
    ) -> list[ConversationHit]: ...

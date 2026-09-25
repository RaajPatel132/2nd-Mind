"""Typed, versioned turn events: the source of truth for the glass box (FR-9.2).

Every event carries ``type`` (the discriminator) and ``v`` (its schema version). Add a field
with a default to evolve an event; bump ``v`` for anything that changes meaning. ``retrieval``
and ``citations`` arrived with recall (S3.13); ``retrieval`` was reshaped before it was ever
emitted, so it is still ``v=1``.

A ``step`` event records one agent step that ran (ADR-0029): the Trail in the UI is drawn from
these, and each step's other events are written with it.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from secondmind.core.memory_model import (
    EntityKind,
    KeyKind,
    Kind,
    Modality,
    ReconcileDecision,
    Sensitivity,
    TimeClock,
    TimePrecision,
)
from secondmind.core.usage import Usage


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    v: int = 1


class Intent(StrEnum):
    SAVE = "save"
    RECALL = "recall"
    SAVE_AND_RECALL = "save_and_recall"
    CORRECT = "correct"
    CHIT_CHAT = "chit_chat"


class Layer(StrEnum):
    CORE = "core"
    QUICK = "quick"
    ARCHIVE = "archive"


class PolicyDecision(StrEnum):
    ALLOWED = "allowed"
    HELD = "held"
    BLOCKED = "blocked"


class AgentStep(StrEnum):
    """The agent steps a turn can report, in the UI's catalogue (docs/design/system.md §8).

    ``plan``, ``search``, ``rank`` and ``triggers`` are recall's (S3); ``fetch`` (S4) is
    reserved: in the schema so the UI's catalogue is complete, not emitted yet.
    """

    UNDERSTAND = "understand"
    EXTRACT = "extract"
    DATES = "dates"
    ENTITIES = "entities"
    RECONCILE = "reconcile"
    ENRICH = "enrich"
    GUARD = "guard"
    SAVE = "save"
    ANSWER = "answer"
    UNDO = "undo"
    CONFIRM = "confirm"
    PLAN = "plan"
    SEARCH = "search"
    RANK = "rank"
    TRIGGERS = "triggers"
    FETCH = "fetch"


class StepStatus(StrEnum):
    DONE = "done"
    HELD = "held"
    REFUSED = "refused"
    FAILED = "failed"


# ------------------------------------------------------------------ S1: emitted now


class IntentEvent(_Event):
    """What the agent decided the message is (FR-1.2)."""

    type: Literal["intent"] = "intent"
    intent: Intent
    confidence: float = Field(ge=0, le=1)
    reason: str
    source: Literal["stub", "model", "rule"]


class FallbackInfo(BaseModel):
    """Set on a model call that was served by the step's fallback model (FR-14.4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_provider: str
    from_model: str
    reason: str


class ModelCallEvent(_Event):
    """One model call: who served it, what it cost, how long it took (FR-14.8, FR-19.2)."""

    type: Literal["model_call"] = "model_call"
    step: str
    provider: str
    model: str
    prompt: str | None = None
    started_at: datetime
    latency_ms: int = Field(ge=0)
    time_to_first_token_ms: int | None = None
    attempts: int = Field(ge=0)
    usage: Usage
    fallback: FallbackInfo | None = None
    cache_hits: int | None = Field(
        default=None, description="Embeddings reused from the content-hash cache (FR-14.6)."
    )


class ErrorEvent(_Event):
    """A turn-ending or step-level error, with a stable code and a user-safe message."""

    type: Literal["error"] = "error"
    code: str
    message: str
    step: str | None = None
    retryable: bool = False


# ------------------------------------------------------------------ S2: decisions and diffs


class TimeResolution(BaseModel):
    """One time expression, resolved by code (never the model): expression -> value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    expression: str
    clock: TimeClock
    value: str
    end: str | None = None
    precision: TimePrecision
    rrule: str | None = None
    now: datetime
    timezone: str
    rule: str
    assumed: bool = False
    alternative: str | None = None
    memory: str | None = None
    item_id: uuid.UUID | None = Field(
        default=None, description="The memory this date was written to (for a one-tap fix)."
    )
    anchor: str | None = Field(
        default=None, description="Recall: the event a window was computed from ('Goa trip')."
    )


class EntityResolution(BaseModel):
    """How a mention ("my wife", "Severance") was matched to an entity, or why one was made."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mention: str
    entity_id: uuid.UUID | None
    entity_kind: EntityKind
    display_name: str
    outcome: Literal["matched", "new", "ambiguous", "updated"]
    created: bool
    candidates: list[str] = []
    rationale: str


class Classification(BaseModel):
    """What one proposed memory was taken to be."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    kind: Kind
    subtype: str | None = None
    format: str | None = None
    state: str | None = None
    category: str | None = None
    modality: Modality = Modality.ASSERTED
    sensitivity: Sensitivity = Sensitivity.NORMAL
    layer: Layer = Layer.ARCHIVE
    rationale: str
    item_id: uuid.UUID | None = None


class Normalisation(BaseModel):
    """A slug the model proposed, and what normalisation chose (reuse beats invention)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    vocab: Literal["category", "subtype", "predicate", "relation"]
    proposed: str
    chosen: str
    reused: bool
    how: str = ""


class ReconcileInfo(BaseModel):
    """How a proposed memory relates to what is stored (S2.8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: ReconcileDecision
    candidate_id: uuid.UUID | None = None
    candidate_title: str | None = None
    score: float | None = None
    rule: str = ""


class Reconciliation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    info: ReconcileInfo


class DecisionEvent(_Event):
    """Decision panel: kinds, date and entity resolutions, reconciliation, rationale."""

    type: Literal["decision"] = "decision"
    summary: str
    classifications: list[Classification] = []
    time_resolutions: list[TimeResolution] = []
    entity_resolutions: list[EntityResolution] = []
    normalisations: list[Normalisation] = []
    reconciliations: list[Reconciliation] = []
    not_written: list[str] = []
    rules_applied: list[str] = []
    rationale: str = ""
    decided_by: dict[str, str] = Field(
        default={}, description="step -> 'provider:model' that made the decision (FR-14.8)."
    )


class FieldChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    before: Any = None
    after: Any = None


DiffOp = Literal[
    "added",
    "updated",
    "removed",
    "superseded",
    "fulfilled",
    "corrected",
    "held",
    "not_written",
    "conflict",
]


class DiffEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    op: DiffOp
    layer: Layer
    item_id: uuid.UUID | None = None
    entity_id: uuid.UUID | None = None
    title: str
    changes: list[FieldChange] = []
    reason: str = ""
    rule_id: str | None = None
    reconcile: ReconcileInfo | None = None
    held_write_id: uuid.UUID | None = None


class MemoryDiffEvent(_Event):
    """Memory diff panel, built from the write log so it can't disagree with what happened."""

    type: Literal["memory_diff"] = "memory_diff"
    entries: list[DiffEntry] = []
    undo_of: uuid.UUID | None = None


# ------------------------------------------------------------------ S3: recall


class Shape(StrEnum):
    """What kind of question a sub-query is (S3.4). Code maps each shape to its tools."""

    EXACT = "exact"
    LIST = "list"
    LATEST = "latest"
    HISTORY = "history"
    TIME_WINDOW = "time_window"
    ORDER = "order"
    COUNT = "count"
    SET = "set"
    ENTITY = "entity"
    SEMANTIC = "semantic"
    WHY = "why"
    SITUATIONAL = "situational"
    CONVERSATION = "conversation"


class FoundBy(BaseModel):
    """One channel that found a candidate, and the candidate's rank in it (1 = top)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    channel: str
    rank: int = Field(ge=1)


class RetrievalCandidate(BaseModel):
    """A memory (or, for conversation recall, a past turn) that reached fusion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = Field(default=None, description="Set for a conversation snippet.")
    title: str
    kind: Kind | None = None
    state: str | None = None
    layer: Layer = Layer.ARCHIVE
    found_by: list[FoundBy] = []
    matched_key: KeyKind | None = Field(
        default=None, description="The key kind the search matched on ('matched via cue key')."
    )
    lexical_score: float | None = None
    dense_score: float | None = None
    fused_score: float | None = Field(default=None, description="RRF across every channel.")
    rerank_score: float | None = None
    rerank_reason: str = ""
    soft_only: bool = Field(default=False, description="Found by the soft channel only.")
    demoted: bool = Field(default=False, description="History: superseded, moved, dropped.")
    selected: bool = False
    cited: bool = False
    reason: str = ""


class ToolRun(BaseModel):
    """One retrieval tool call of a sub-query."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str
    arguments: dict[str, str] = {}
    count: int = 0
    latency_ms: int = 0
    error: str | None = None


class RelaxStep(BaseModel):
    """One loosening of the filters after every filtered channel came back empty (S3.6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step: Literal["category", "subtype", "state", "window_month", "window_wide", "entity"]
    change: str
    count: int


class EntityTrace(BaseModel):
    """How a mention in the question was resolved, including relation paths followed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mention: str
    outcome: Literal["matched", "unknown", "no_relation"]
    path: list[str] = Field(default=[], description="e.g. ['Nisha', 'spouse_of', 'Rohan'].")
    entity_ids: list[uuid.UUID] = []
    names: list[str] = []


class GroupValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    value: float
    count: int


class AggregateTrace(BaseModel):
    """An exact number from SQL, with the ids it counted (S3.5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    op: Literal["count", "sum", "min", "max", "average"]
    field: str | None = None
    group_by: str | None = None
    value: float | None = None
    groups: list[GroupValue] = []
    counted_ids: list[uuid.UUID] = []


class CountCheck(BaseModel):
    """Soft-channel hits that look like what was counted but weren't (S3.6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    extra_ids: list[uuid.UUID] = []
    note: str = ""
    offer: str | None = None
    fix: dict[str, str] = Field(default={}, description="The reclassification a 'yes' applies.")


class Expansion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Literal["planner", "code"]
    core_entry: str | None = None
    reason: str


class SubQueryTrace(BaseModel):
    """One part of the plan, what ran for it, and what it found."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int
    shape: Shape
    question: str
    topic: str = ""
    filters: dict[str, str] = {}
    dropped: list[str] = Field(default=[], description="Filter hints dropped as unknown.")
    windows: list[TimeResolution] = []
    entities: list[EntityTrace] = []
    tools: list[ToolRun] = []
    soft_query: str | None = None
    relaxation: list[RelaxStep] = []
    expansion: Expansion | None = None
    aggregate: AggregateTrace | None = None
    count_check: CountCheck | None = None
    candidates: list[RetrievalCandidate] = []
    abstained: bool = False


class TimingSpan(BaseModel):
    """A non-model span for the waterfall (fusion, selection)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    started_at: datetime
    latency_ms: int = Field(ge=0)


class RetrievalEvent(_Event):
    """Retrieval panel: the plan, what each tool and the soft channel found, fusion, relaxation,
    rerank and what reached the answer (S3.13)."""

    type: Literal["retrieval"] = "retrieval"
    question: str
    plan_source: Literal["model", "retry", "fallback"] = "model"
    plan_note: str = ""
    sub_queries: list[SubQueryTrace] = []
    soft_channel: bool = True
    rerank: Literal["model", "disabled", "failed", "skipped"] = "model"
    rerank_note: str = ""
    timings: list[TimingSpan] = []
    explanation: str = ""


class Citation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    marker: int = Field(ge=1)
    kind: Literal["item", "turn"]
    item_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = None
    title: str


class CitationsEvent(_Event):
    """The ``[n]`` markers of the reply, mapped by code to memories or past turns (S3.8).
    A marker that matched no evidence was stripped from the reply and is counted here."""

    type: Literal["citations"] = "citations"
    citations: list[Citation] = []
    stripped: int = Field(default=0, ge=0)
    evidence: int = Field(default=0, ge=0)


class PolicyVerdict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: PolicyDecision
    rule_id: str
    reason: str


class ToolCallEvent(_Event):
    """Tool calls panel: tool, summarised arguments and result, policy verdict. Retrieval tools
    are ``read`` (no write policy applies); memory writer ops are ``write``."""

    type: Literal["tool_call"] = "tool_call"
    tool: str
    arguments: dict[str, str] = {}
    result_summary: str = ""
    policy: PolicyVerdict | None = None
    latency_ms: int | None = None
    access: Literal["read", "write"] = "write"
    started_at: datetime | None = None
    count: int | None = None
    error: str | None = None


class PolicyEvent(_Event):
    """A write-policy decision on one proposed operation (FR-4.6)."""

    type: Literal["policy"] = "policy"
    op: str
    target: str | None = None
    verdict: PolicyVerdict


class StepEvent(_Event):
    """One agent step that ran: how it ended and how long it took (ADR-0029)."""

    type: Literal["step"] = "step"
    step: AgentStep
    status: StepStatus
    started_at: datetime
    latency_ms: int = Field(ge=0)


TurnEvent = Annotated[
    IntentEvent
    | DecisionEvent
    | MemoryDiffEvent
    | RetrievalEvent
    | CitationsEvent
    | ToolCallEvent
    | PolicyEvent
    | ModelCallEvent
    | ErrorEvent
    | StepEvent,
    Field(discriminator="type"),
]

TURN_EVENT_ADAPTER: TypeAdapter[TurnEvent] = TypeAdapter(TurnEvent)


def parse_turn_event(data: object) -> TurnEvent:
    """Validate a stored event payload back into its typed model."""
    return TURN_EVENT_ADAPTER.validate_python(data)

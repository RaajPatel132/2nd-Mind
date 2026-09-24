"""Typed, versioned turn events: the source of truth for the glass box (FR-9.2).

Every event carries ``type`` (the discriminator) and ``v`` (its schema version). Add a field
with a default to evolve an event; bump ``v`` for anything that changes meaning. ``retrieval``
is defined but not emitted until S3.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from secondmind.core.memory_model import (
    EntityKind,
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
    "added", "updated", "removed", "superseded", "fulfilled", "held", "not_written", "conflict"
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


# ------------------------------------------------------------------ S3: defined, not yet emitted


class RetrievalCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: uuid.UUID
    title: str
    layer: Layer
    lexical_score: float | None = None
    dense_score: float | None = None
    rerank_score: float | None = None
    fused_score: float | None = None
    selected: bool = False
    reason: str = ""


class RetrievalEvent(_Event):
    """Retrieval panel: plan, filters, candidates with scores, what reached the answer."""

    type: Literal["retrieval"] = "retrieval"
    query: str
    filters: dict[str, str] = {}
    layers: list[Layer] = []
    candidates: list[RetrievalCandidate] = []
    explanation: str = ""


class PolicyVerdict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: PolicyDecision
    rule_id: str
    reason: str


class ToolCallEvent(_Event):
    """Tool calls panel: tool, summarised arguments and result, policy verdict."""

    type: Literal["tool_call"] = "tool_call"
    tool: str
    arguments: dict[str, str] = {}
    result_summary: str = ""
    policy: PolicyVerdict | None = None
    latency_ms: int | None = None


class PolicyEvent(_Event):
    """A write-policy decision on one proposed operation (FR-4.6)."""

    type: Literal["policy"] = "policy"
    op: str
    target: str | None = None
    verdict: PolicyVerdict


TurnEvent = Annotated[
    IntentEvent
    | DecisionEvent
    | MemoryDiffEvent
    | RetrievalEvent
    | ToolCallEvent
    | PolicyEvent
    | ModelCallEvent
    | ErrorEvent,
    Field(discriminator="type"),
]

TURN_EVENT_ADAPTER: TypeAdapter[TurnEvent] = TypeAdapter(TurnEvent)


def parse_turn_event(data: object) -> TurnEvent:
    """Validate a stored event payload back into its typed model."""
    return TURN_EVENT_ADAPTER.validate_python(data)

"""Request and response shapes of the public /v1 API (the OpenAPI snapshot is built from these)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from secondmind.agent import StepModel, StoredEvent, TraceStatus, Turn, TurnKind, TurnStatus
from secondmind.auth import User, Workspace, WorkspaceKind
from secondmind.core import AgentStep, Layer, TurnEvent, UsageTotals
from secondmind.corrections import CorrectionChanges
from secondmind.memory import (
    EntityRecord,
    HeldWriteRecord,
    ItemEntityRecord,
    ItemRecord,
    LinkRecord,
    TriggerRecord,
)
from secondmind.metering import QuotaUsage, Tier


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class ErrorBody(_Out):
    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable, safe to show to users.")
    request_id: str | None = None


class ErrorResponse(_Out):
    error: ErrorBody


class TraceLink(_Out):
    status: TraceStatus
    url: str | None = Field(
        default=None, description="Link to the raw trace; null when unavailable or disabled."
    )


class TurnError(_Out):
    code: str
    message: str


class TurnOut(_Out):
    id: uuid.UUID
    workspace_id: uuid.UUID
    input: str
    output: str | None
    status: TurnStatus
    started_at: datetime
    finished_at: datetime | None
    config_hash: str
    prompt_versions: list[str]
    models: dict[str, StepModel]
    usage: UsageTotals
    trace: TraceLink
    error: TurnError | None = None
    kind: TurnKind = Field(
        default=TurnKind.USER,
        description="user (a message), undo, confirm (a held write) or system (a background job).",
    )
    parent_turn_id: uuid.UUID | None = Field(
        default=None, description="The undone turn, or the turn whose held write was confirmed."
    )

    @classmethod
    def of(cls, turn: Turn, trace_url: str | None) -> "TurnOut":
        error = None
        if turn.error_code:
            error = TurnError(code=turn.error_code, message=turn.error_message or "")
        return cls(
            id=turn.id,
            workspace_id=turn.workspace_id,
            input=turn.input,
            output=turn.output,
            status=turn.status,
            started_at=turn.started_at,
            finished_at=turn.finished_at,
            config_hash=turn.config_hash,
            prompt_versions=turn.prompt_versions,
            models=turn.models,
            usage=turn.usage,
            trace=TraceLink(
                status=turn.trace_status,
                url=trace_url if turn.trace_status is TraceStatus.RECORDED else None,
            ),
            error=error,
            kind=turn.kind,
            parent_turn_id=turn.parent_turn_id,
        )


class TurnPage(_Out):
    items: list[TurnOut] = Field(description="Newest first.")
    next_before: uuid.UUID | None = Field(
        default=None, description="Pass as `before` to get the next (older) page."
    )


class TurnEventOut(_Out):
    seq: int
    created_at: datetime
    event: TurnEvent

    @classmethod
    def of(cls, stored: StoredEvent) -> "TurnEventOut":
        return cls(seq=stored.seq, created_at=stored.created_at, event=stored.event)


class TurnEventsOut(_Out):
    turn_id: uuid.UUID
    events: list[TurnEventOut] = Field(description="In emission order (seq ascending).")


class HeldWriteOut(_Out):
    """A write the policy held for confirmation (S2.3)."""

    id: uuid.UUID
    turn_id: uuid.UUID
    title: str
    reason: str
    rule_id: str
    layer: Layer
    status: Literal["pending", "confirmed", "rejected"]
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_turn_id: uuid.UUID | None = None

    @classmethod
    def of(cls, held: HeldWriteRecord) -> "HeldWriteOut":
        return cls(
            id=held.id,
            turn_id=held.turn_id,
            title=held.title,
            reason=held.reason,
            rule_id=held.rule_id,
            layer=held.layer,
            status=held.status,
            created_at=held.created_at,
            resolved_at=held.resolved_at,
            resolved_turn_id=held.resolved_turn_id,
        )


class HeldWritesOut(_Out):
    items: list[HeldWriteOut]


class UpcomingEntryOut(_Out):
    item_id: uuid.UUID
    title: str
    kind: str
    state: str
    at: datetime
    until: datetime | None
    via: str = Field(description="occurred, routine, due or trigger (a reminder)")
    routine: bool
    trigger_id: uuid.UUID | None = Field(
        default=None, description="For a reminder (via trigger): what Snooze moves."
    )


class UpcomingDayOut(_Out):
    day: str = Field(description="The local date, YYYY-MM-DD, in the workspace timezone.")
    entries: list[UpcomingEntryOut]


class UndatedTaskOut(_Out):
    item_id: uuid.UUID
    title: str
    state: str


class UpcomingOut(_Out):
    timezone: str
    start: datetime
    end: datetime
    days: list[UpcomingDayOut]
    undated: list[UndatedTaskOut]
    due_soon: int = Field(description="Entries in the next 24 hours.")
    note: str | None = Field(description="The due-soon note for the chat, built in code.")


class DevSeedOut(_Out):
    seeded: bool
    items: int


class SnoozeIn(BaseModel):
    """Snooze a reminder to a new time (S3.14): free text read by the resolver."""

    model_config = ConfigDict(extra="forbid")

    date_expression: str = Field(min_length=1, max_length=200, examples=["2026-10-07 19:00"])


class ItemEditIn(BaseModel):
    """A glass-box edit of one memory (S3.12). Only the fields given change."""

    model_config = ConfigDict(extra="forbid")

    kind: str | None = None
    subtype: str | None = None
    category: str | None = Field(default=None, max_length=200)
    tags: list[str] | None = Field(default=None, max_length=20)
    format: str | None = None
    layer: Literal["core", "quick", "archive"] | None = None
    state: str | None = None
    date_expression: str | None = Field(
        default=None, max_length=100, description="Free text read by the resolver: 'Friday'."
    )
    date_clock: Literal["occurred", "due", "valid"] | None = None
    delete: bool = False

    def changes(self) -> CorrectionChanges:
        return CorrectionChanges.model_validate(self.model_dump(exclude={"delete"}))


class ItemDetailOut(_Out):
    """A plain view of one memory item, its entity roles, links and triggers (S2.11)."""

    item: ItemRecord
    entities: list[ItemEntityRecord]
    links: list[LinkRecord]
    triggers: list[TriggerRecord]


class EntityDetailOut(_Out):
    entity: EntityRecord
    item_ids: list[uuid.UUID]


class DevLoginIn(BaseModel):
    """Optional: log in as another dev user (dev auth only), e.g. a fresh one per E2E test."""

    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(default=None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+$")


class CreateTurnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=100_000)
    model: str | None = Field(
        default=None,
        max_length=200,
        description="A picker model (`id` from `/v1/meta` `picker.choices`) for every chat step "
        "of this turn. Omitted: the configured per-step routing.",
    )


class WorkspaceOut(_Out):
    id: uuid.UUID
    kind: WorkspaceKind
    timezone: str
    created_at: datetime

    @classmethod
    def of(cls, ws: Workspace) -> "WorkspaceOut":
        return cls(id=ws.id, kind=ws.kind, timezone=ws.timezone, created_at=ws.created_at)


class UserOut(_Out):
    id: uuid.UUID
    email: str | None


class MeOut(_Out):
    user: UserOut
    workspaces: list[WorkspaceOut]

    @classmethod
    def of(cls, user: User, workspaces: list[Workspace]) -> "MeOut":
        return cls(
            user=UserOut(id=user.id, email=user.email),
            workspaces=[WorkspaceOut.of(w) for w in workspaces],
        )


class UsageOut(_Out):
    """The signed-in user's token quota, as it stands now (FR-12.5). Read only until S4."""

    tier: Tier
    limit_tokens: int
    used_tokens: int
    remaining_tokens: int

    @classmethod
    def of(cls, usage: QuotaUsage) -> "UsageOut":
        return cls(**usage.model_dump())


class RouteOut(_Out):
    step: str
    provider: str
    model: str
    fallback: str | None
    prompt: str | None
    timeout_s: float


class ModelChoiceOut(_Out):
    id: str = Field(description="provider:model, as sent in a turn's `model`.")
    label: str
    provider: str
    provider_label: str
    weight: float = Field(
        description="Quota tokens per token on this model, against the baseline (1 = baseline)."
    )
    simulated: bool = Field(description="The fake provider stands in for it (no credentials).")
    available: bool = Field(description="False when it can't be used (live mode, no key).")


class PickerOut(_Out):
    """The model picker (ADR-0030): choices in display order, grouped by provider."""

    default: str
    baseline: str
    baseline_label: str
    choices: list[ModelChoiceOut]


class MetaOut(_Out):
    name: str
    version: str
    env: str
    config_hash: str
    config_hash_short: str
    provider_mode: str
    price_version: str
    picker: PickerOut | None = Field(default=None, description="Null when no picker is configured.")
    routes: list[RouteOut]
    prompts: list[str]
    substitutions: list[str]
    dev_auth: bool
    tracing_enabled: bool


class CheckOut(_Out):
    ok: bool
    detail: str


class ReadyOut(_Out):
    status: Literal["ready", "not_ready"]
    checks: dict[str, CheckOut]


class HealthOut(_Out):
    status: Literal["ok"]


# ------------------------------------------------------------------ SSE frames (POST .../turns)


class SseTurnStarted(_Out):
    turn_id: uuid.UUID
    workspace_id: uuid.UUID
    started_at: datetime


class SseToken(_Out):
    text: str


class SseStepStarted(_Out):
    """An agent step began (ADR-0029). Its ``step`` event follows in a ``turn.event`` frame."""

    step: AgentStep
    at: datetime


class SseTurnEvent(_Out):
    """A turn event, as it was persisted (the same ``seq`` and body as ``/events``)."""

    seq: int
    event: TurnEvent


class SseTurnCompleted(_Out):
    turn_id: uuid.UUID
    usage: UsageTotals
    turn: TurnOut
    quota: UsageOut | None = Field(
        default=None, description="The user's quota after this turn (null if it can't be read)."
    )


class SseTurnFailed(_Out):
    turn_id: uuid.UUID
    error: TurnError
    usage: UsageTotals
    turn: TurnOut
    quota: UsageOut | None = Field(
        default=None, description="The user's quota after this turn (null if it can't be read)."
    )


SSE_EVENTS: dict[str, type[_Out]] = {
    "turn.started": SseTurnStarted,
    "token": SseToken,
    "step.started": SseStepStarted,
    "turn.event": SseTurnEvent,
    "turn.completed": SseTurnCompleted,
    "turn.failed": SseTurnFailed,
}

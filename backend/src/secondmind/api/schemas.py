"""Request and response shapes of the public /v1 API (the OpenAPI snapshot is built from these)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from secondmind.agent import StepModel, StoredEvent, TraceStatus, Turn, TurnStatus
from secondmind.auth import User, Workspace, WorkspaceKind
from secondmind.core import TurnEvent, UsageTotals


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


class CreateTurnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=100_000)


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


class RouteOut(_Out):
    step: str
    provider: str
    model: str
    fallback: str | None
    prompt: str | None
    timeout_s: float


class MetaOut(_Out):
    name: str
    version: str
    env: str
    config_hash: str
    config_hash_short: str
    provider_mode: str
    price_version: str
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


class SseTurnCompleted(_Out):
    turn_id: uuid.UUID
    usage: UsageTotals
    turn: TurnOut


class SseTurnFailed(_Out):
    turn_id: uuid.UUID
    error: TurnError
    usage: UsageTotals
    turn: TurnOut


SSE_EVENTS: dict[str, type[_Out]] = {
    "turn.started": SseTurnStarted,
    "token": SseToken,
    "turn.completed": SseTurnCompleted,
    "turn.failed": SseTurnFailed,
}

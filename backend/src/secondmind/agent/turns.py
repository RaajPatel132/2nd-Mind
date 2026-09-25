"""Turns and their events: the record of one message and everything the agent did for it."""

import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from secondmind.core import ModelCallEvent, TurnEvent, UsageTotals, WorkspaceScope


class TurnStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TurnKind(StrEnum):
    """What started a turn: a message, an undo, a confirmation of a held write, or the system
    (background jobs). Every kind is stored, auditable and undoable the same way."""

    USER = "user"
    UNDO = "undo"
    CONFIRM = "confirm"
    SYSTEM = "system"


class TraceStatus(StrEnum):
    """Whether the turn's trace reached the trace backend (FR-9.2)."""

    RECORDED = "recorded"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class StepModel(BaseModel):
    """Which provider and model served a step, and with which prompt version (FR-19.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    prompt: str | None = None
    fallback_from: str | None = None


class Turn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    input: str
    output: str | None
    status: TurnStatus
    started_at: datetime
    finished_at: datetime | None
    config_hash: str
    prompt_versions: list[str]
    models: dict[str, StepModel]
    usage: UsageTotals
    trace_status: TraceStatus
    error_code: str | None = None
    error_message: str | None = None
    kind: TurnKind = TurnKind.USER
    parent_turn_id: uuid.UUID | None = None


class StoredEvent(BaseModel):
    """An event as persisted: its position in the turn and when it was recorded."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int
    created_at: datetime
    event: TurnEvent


class TurnOutcome(BaseModel):
    """How a turn ended; written once when it finishes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: TurnStatus
    output: str | None
    usage: UsageTotals
    models: dict[str, StepModel]
    prompt_versions: list[str]
    trace_status: TraceStatus
    finished_at: datetime
    error_code: str | None = None
    error_message: str | None = None


class TurnStore(Protocol):
    """Persistence port for turns. Implementations are bound to one workspace scope."""

    async def create(
        self,
        *,
        turn_id: uuid.UUID,
        text: str,
        config_hash: str,
        started_at: datetime,
        kind: TurnKind = TurnKind.USER,
        parent_turn_id: uuid.UUID | None = None,
    ) -> Turn: ...

    async def redact_input(self, turn_id: uuid.UUID, text: str) -> None:
        """Replace the stored input (a secret found after the turn started)."""
        ...

    async def append(self, turn_id: uuid.UUID, event: TurnEvent) -> StoredEvent: ...

    async def record_model_call(self, turn_id: uuid.UUID, event: ModelCallEvent) -> StoredEvent:
        """Persist the event and its usage-ledger row together (FR-12.1)."""
        ...

    async def append_many(
        self, turn_id: uuid.UUID, events: Sequence[TurnEvent]
    ) -> list[StoredEvent]:
        """Persist several events in order, in one write; every ``model_call`` among them gets
        its usage-ledger row in the same transaction (a step and its events, ADR-0029)."""
        ...

    async def finish(self, turn_id: uuid.UUID, outcome: TurnOutcome) -> Turn: ...

    async def get(self, turn_id: uuid.UUID) -> Turn | None: ...

    async def recent(self, *, limit: int, before: uuid.UUID | None = None) -> list[Turn]:
        """Newest first; ``before`` is the id of the last turn on the previous page."""
        ...

    async def events(self, turn_id: uuid.UUID) -> list[StoredEvent]: ...


TurnStoreFactory = Callable[[WorkspaceScope], TurnStore]

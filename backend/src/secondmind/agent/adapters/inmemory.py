"""In-memory turn store (tests and offline evals): the TurnStore port without a database."""

import uuid
from collections.abc import Callable, Sequence
from datetime import datetime

from secondmind.agent.turns import (
    StoredEvent,
    TraceStatus,
    Turn,
    TurnKind,
    TurnOutcome,
    TurnStatus,
)
from secondmind.core import (
    ModelCallEvent,
    NotFoundError,
    TurnEvent,
    UsageTotals,
    WorkspaceScope,
    utc_now,
)
from secondmind.metering import LedgerEntry


class InMemoryTurnStore:
    """TurnStore bound to one scope; rows live in a shared dict like one database."""

    def __init__(self, db: "InMemoryTurns", scope: WorkspaceScope) -> None:
        self._db = db
        self._scope = scope

    async def create(
        self,
        *,
        turn_id: uuid.UUID,
        text: str,
        config_hash: str,
        started_at: datetime,
        kind: TurnKind = TurnKind.USER,
        parent_turn_id: uuid.UUID | None = None,
    ) -> Turn:
        turn = Turn(
            kind=kind,
            parent_turn_id=parent_turn_id,
            id=turn_id,
            workspace_id=self._scope.workspace_id,
            user_id=self._scope.user_id,
            input=text,
            output=None,
            status=TurnStatus.RUNNING,
            started_at=started_at,
            finished_at=None,
            config_hash=config_hash,
            prompt_versions=[],
            models={},
            usage=UsageTotals(),
            trace_status=TraceStatus.DISABLED,
        )
        self._db.turns[turn_id] = turn
        self._db.events[turn_id] = []
        return turn

    def _own(self, turn_id: uuid.UUID) -> Turn:
        turn = self._db.turns.get(turn_id)
        if turn is None or turn.workspace_id != self._scope.workspace_id:
            raise NotFoundError(f"turn {turn_id} not found")
        return turn

    async def redact_input(self, turn_id: uuid.UUID, text: str) -> None:
        self._db.turns[turn_id] = self._own(turn_id).model_copy(update={"input": text})

    async def append(self, turn_id: uuid.UUID, event: TurnEvent) -> StoredEvent:
        self._own(turn_id)
        stored = StoredEvent(
            seq=len(self._db.events[turn_id]) + 1, created_at=self._db.clock(), event=event
        )
        self._db.events[turn_id].append(stored)
        return stored

    async def record_model_call(self, turn_id: uuid.UUID, event: ModelCallEvent) -> StoredEvent:
        stored = await self.append(turn_id, event)
        self._db.ledger.append(
            LedgerEntry.from_model_call(
                workspace_id=self._scope.workspace_id, turn_id=turn_id, event=event
            )
        )
        return stored

    async def append_many(
        self, turn_id: uuid.UUID, events: Sequence[TurnEvent]
    ) -> list[StoredEvent]:
        stored: list[StoredEvent] = []
        for event in events:
            if isinstance(event, ModelCallEvent):
                stored.append(await self.record_model_call(turn_id, event))
            else:
                stored.append(await self.append(turn_id, event))
        return stored

    async def finish(self, turn_id: uuid.UUID, outcome: TurnOutcome) -> Turn:
        update = {name: getattr(outcome, name) for name in TurnOutcome.model_fields}
        turn = self._own(turn_id).model_copy(update=update)
        self._db.turns[turn_id] = turn
        return turn

    async def get(self, turn_id: uuid.UUID) -> Turn | None:
        turn = self._db.turns.get(turn_id)
        return turn if turn and turn.workspace_id == self._scope.workspace_id else None

    async def recent(self, *, limit: int, before: uuid.UUID | None = None) -> list[Turn]:
        rows = sorted(
            (t for t in self._db.turns.values() if t.workspace_id == self._scope.workspace_id),
            key=lambda t: t.id,
            reverse=True,
        )
        if before is not None:
            rows = [t for t in rows if t.id < before]
        return rows[:limit]

    async def events(self, turn_id: uuid.UUID) -> list[StoredEvent]:
        if await self.get(turn_id) is None:
            return []
        return list(self._db.events[turn_id])


class InMemoryTurns:
    def __init__(self, clock: Callable[[], datetime] = utc_now) -> None:
        self.turns: dict[uuid.UUID, Turn] = {}
        self.events: dict[uuid.UUID, list[StoredEvent]] = {}
        self.ledger: list[LedgerEntry] = []
        self.clock = clock

    def store(self, scope: WorkspaceScope) -> InMemoryTurnStore:
        return InMemoryTurnStore(self, scope)

    async def tokens_used(self, user_id: uuid.UUID, workspace_ids: Sequence[uuid.UUID]) -> int:
        """A ``LedgerReader``: the ledger charged in these workspaces (the tests' users own
        their workspaces)."""
        del user_id
        wanted = set(workspace_ids)
        return sum(
            e.input_tokens + e.cached_input_tokens + e.output_tokens
            for e in self.ledger
            if e.workspace_id in wanted
        )

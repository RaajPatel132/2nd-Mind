"""In-memory test doubles for ports (no database)."""

import uuid
from datetime import datetime

from secondmind.agent import StoredEvent, Turn, TurnOutcome, TurnStatus
from secondmind.agent.turns import TraceStatus
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
        self, *, turn_id: uuid.UUID, text: str, config_hash: str, started_at: datetime
    ) -> Turn:
        turn = Turn(
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

    async def append(self, turn_id: uuid.UUID, event: TurnEvent) -> StoredEvent:
        self._own(turn_id)
        stored = StoredEvent(
            seq=len(self._db.events[turn_id]) + 1, created_at=utc_now(), event=event
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
    def __init__(self) -> None:
        self.turns: dict[uuid.UUID, Turn] = {}
        self.events: dict[uuid.UUID, list[StoredEvent]] = {}
        self.ledger: list[LedgerEntry] = []

    def store(self, scope: WorkspaceScope) -> InMemoryTurnStore:
        return InMemoryTurnStore(self, scope)


class InMemoryIdentity:
    """IdentityStore double."""

    def __init__(self) -> None:
        from secondmind.auth import User, Workspace  # noqa: PLC0415 - keep module import light

        self.users: dict[uuid.UUID, User] = {}
        self.workspaces: dict[uuid.UUID, Workspace] = {}

    async def ensure_user_with_private_workspace(self, *, email: str, timezone: str):  # type: ignore[no-untyped-def]
        from secondmind.auth import User, Workspace, WorkspaceKind  # noqa: PLC0415
        from secondmind.core import new_id  # noqa: PLC0415

        user = next((u for u in self.users.values() if u.email == email), None)
        if user is None:
            user = User(id=new_id(), email=email, created_at=utc_now())
            self.users[user.id] = user
        ws = next(
            (
                w
                for w in self.workspaces.values()
                if w.owner_user_id == user.id and w.kind == "private"
            ),
            None,
        )
        if ws is None:
            ws = Workspace(
                id=new_id(),
                owner_user_id=user.id,
                kind=WorkspaceKind.PRIVATE,
                timezone=timezone,
                created_at=utc_now(),
            )
            self.workspaces[ws.id] = ws
        return user, ws

    async def get_user(self, user_id: uuid.UUID):  # type: ignore[no-untyped-def]
        return self.users.get(user_id)

    async def get_workspace(self, workspace_id: uuid.UUID):  # type: ignore[no-untyped-def]
        return self.workspaces.get(workspace_id)

    async def workspaces_for(self, user_id: uuid.UUID):  # type: ignore[no-untyped-def]
        return [w for w in self.workspaces.values() if w.owner_user_id == user_id]

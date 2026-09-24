"""SQL implementation of the TurnStore port. Every instance is bound to one WorkspaceScope."""

import uuid
from typing import Any

from sqlalchemy import select, update

from secondmind.agent import (
    StepModel,
    StoredEvent,
    TraceStatus,
    Turn,
    TurnOutcome,
    TurnStatus,
)
from secondmind.agent.adapters.tables import TurnEventRow, TurnRow
from secondmind.core import (
    ModelCallEvent,
    NotFoundError,
    TurnEvent,
    UsageTotals,
    WorkspaceScope,
    parse_turn_event,
)
from secondmind.memory.adapters import Database
from secondmind.metering import LedgerEntry
from secondmind.metering.adapters import insert_ledger_entry


class SqlTurnStore:
    def __init__(self, db: Database, scope: WorkspaceScope) -> None:
        self._db = db
        self._scope = scope

    async def create(
        self, *, turn_id: uuid.UUID, text: str, config_hash: str, started_at: Any
    ) -> Turn:
        row = TurnRow(
            id=turn_id,
            workspace_id=self._scope.workspace_id,
            user_id=self._scope.user_id,
            input=text,
            status=TurnStatus.RUNNING.value,
            started_at=started_at,
            config_hash=config_hash,
            prompt_versions=[],
            models={},
            trace_status=TraceStatus.DISABLED.value,
        )
        async with self._db.workspace(self._scope) as session:
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return _turn(row)

    async def append(self, turn_id: uuid.UUID, event: TurnEvent) -> StoredEvent:
        async with self._db.workspace(self._scope) as session:
            return await self._insert_event(session, turn_id, event)

    async def record_model_call(self, turn_id: uuid.UUID, event: ModelCallEvent) -> StoredEvent:
        entry = LedgerEntry.from_model_call(
            workspace_id=self._scope.workspace_id, turn_id=turn_id, event=event
        )
        async with self._db.workspace(self._scope) as session:
            stored = await self._insert_event(session, turn_id, event)
            await insert_ledger_entry(session, entry)
            return stored

    async def finish(self, turn_id: uuid.UUID, outcome: TurnOutcome) -> Turn:
        values: dict[str, Any] = {
            "status": outcome.status.value,
            "output": outcome.output,
            "finished_at": outcome.finished_at,
            "input_tokens": outcome.usage.input_tokens,
            "cached_input_tokens": outcome.usage.cached_input_tokens,
            "output_tokens": outcome.usage.output_tokens,
            "cost_usd": outcome.usage.cost_usd,
            "models": {k: v.model_dump(mode="json") for k, v in outcome.models.items()},
            "prompt_versions": outcome.prompt_versions,
            "trace_status": outcome.trace_status.value,
            "error_code": outcome.error_code,
            "error_message": outcome.error_message,
        }
        async with self._db.workspace(self._scope) as session:
            row = (
                await session.execute(
                    update(TurnRow).where(TurnRow.id == turn_id).values(**values).returning(TurnRow)
                )
            ).scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"turn {turn_id} not found")
            return _turn(row)

    async def get(self, turn_id: uuid.UUID) -> Turn | None:
        async with self._db.workspace(self._scope) as session:
            row = await session.get(TurnRow, turn_id)
            return None if row is None else _turn(row)

    async def recent(self, *, limit: int, before: uuid.UUID | None = None) -> list[Turn]:
        stmt = select(TurnRow).order_by(TurnRow.id.desc()).limit(limit)
        if before is not None:
            stmt = stmt.where(TurnRow.id < before)
        async with self._db.workspace(self._scope) as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [_turn(r) for r in rows]

    async def events(self, turn_id: uuid.UUID) -> list[StoredEvent]:
        stmt = (
            select(TurnEventRow).where(TurnEventRow.turn_id == turn_id).order_by(TurnEventRow.seq)
        )
        async with self._db.workspace(self._scope) as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [
                StoredEvent(seq=r.seq, created_at=r.created_at, event=parse_turn_event(r.payload))
                for r in rows
            ]

    async def _insert_event(
        self, session: Any, turn_id: uuid.UUID, event: TurnEvent
    ) -> StoredEvent:
        # Atomic per-turn sequence: the row lock on the turn orders concurrent appends.
        seq = (
            await session.execute(
                update(TurnRow)
                .where(TurnRow.id == turn_id)
                .values(event_count=TurnRow.event_count + 1)
                .returning(TurnRow.event_count)
            )
        ).scalar_one_or_none()
        if seq is None:
            raise NotFoundError(f"turn {turn_id} not found")
        row = TurnEventRow(
            workspace_id=self._scope.workspace_id,
            turn_id=turn_id,
            seq=seq,
            type=event.type,
            v=event.v,
            payload=event.model_dump(mode="json"),
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        return StoredEvent(seq=row.seq, created_at=row.created_at, event=event)


def _turn(row: TurnRow) -> Turn:
    return Turn(
        id=row.id,
        workspace_id=row.workspace_id,
        user_id=row.user_id,
        input=row.input,
        output=row.output,
        status=TurnStatus(row.status),
        started_at=row.started_at,
        finished_at=row.finished_at,
        config_hash=row.config_hash,
        prompt_versions=list(row.prompt_versions or []),
        models={k: StepModel.model_validate(v) for k, v in (row.models or {}).items()},
        usage=UsageTotals(
            input_tokens=row.input_tokens,
            cached_input_tokens=row.cached_input_tokens,
            output_tokens=row.output_tokens,
            cost_usd=row.cost_usd,
        ),
        trace_status=TraceStatus(row.trace_status),
        error_code=row.error_code,
        error_message=row.error_message,
    )

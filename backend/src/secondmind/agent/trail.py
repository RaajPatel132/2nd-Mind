"""The runner's Trail: records each agent step with its events, in one write, and streams
``step.started`` and every persisted event to the client as it happens (ADR-0029)."""

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from secondmind.agent.turns import StoredEvent, TurnStore
from secondmind.core import (
    AgentStep,
    Clock,
    StepEvent,
    StepRun,
    StepStatus,
    TurnEvent,
    utc_now,
)
from secondmind.observability import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StepStarted:
    step: AgentStep
    at: datetime


@dataclass(frozen=True, slots=True)
class EventRecorded:
    stored: StoredEvent


Publish = Callable[[StepStarted | EventRecorded], Awaitable[None]]


class TurnTrail:
    """One turn's steps. Steps don't nest; a step's events are held until it ends."""

    def __init__(
        self,
        store: TurnStore,
        turn_id: uuid.UUID,
        publish: Publish | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self._store = store
        self._turn_id = turn_id
        self._publish = publish
        self._clock = clock
        self._open: StepRun | None = None

    async def emit(self, event: TurnEvent) -> None:
        if self._open is not None:
            self._open.events.append(event)
            return
        await self._write([event])

    @asynccontextmanager
    async def run(self, step: AgentStep) -> AsyncIterator[StepRun]:
        if self._open is not None:
            raise RuntimeError(f"step {step} started inside step {self._open.step}")
        run = StepRun(step=step, started_at=self._clock())
        began = time.perf_counter()
        await self._send(StepStarted(step, run.started_at))
        self._open = run
        try:
            yield run
        except BaseException:
            run.status = StepStatus.FAILED
            raise
        finally:
            self._open = None
            measured = round((time.perf_counter() - began) * 1000)
            latency = measured if run.latency_ms is None else run.latency_ms
            record = StepEvent(
                step=step, status=run.status, started_at=run.started_at, latency_ms=latency
            )
            try:
                await self._write([*run.events, record])
            except Exception:
                # A step that fails to record must not hide the error that ended it.
                log.exception("turn.step_record_failed", step=step.value)

    async def report(
        self,
        step: AgentStep,
        *,
        status: StepStatus,
        started_at: datetime,
        latency_ms: int,
        events: Sequence[TurnEvent] = (),
    ) -> None:
        await self._send(StepStarted(step, started_at))
        record = StepEvent(
            step=step, status=status, started_at=started_at, latency_ms=max(0, latency_ms)
        )
        await self._write([*events, record])

    async def _write(self, events: Sequence[TurnEvent]) -> None:
        for stored in await self._store.append_many(self._turn_id, events):
            await self._send(EventRecorded(stored))

    async def _send(self, item: StepStarted | EventRecorded) -> None:
        if self._publish is not None:
            await self._publish(item)

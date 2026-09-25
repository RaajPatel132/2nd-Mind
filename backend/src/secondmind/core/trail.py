"""The Trail: how a turn reports the agent steps it runs (ADR-0029).

A step is a block of work (``async with trail.run(AgentStep.EXTRACT) as step: ...``). Events
emitted through ``trail.emit`` while a step is open are written together with that step's
``step`` event when it ends, in one write. ``report`` records a step whose timing was measured
elsewhere (the save half of a memory commit). Only steps that ran are ever reported.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from secondmind.core.clock import utc_now
from secondmind.core.events import AgentStep, StepStatus, TurnEvent


@dataclass(slots=True)
class StepRun:
    """One open step. Set ``status`` (default done) or ``latency_ms`` (default: measured)."""

    step: AgentStep
    started_at: datetime
    status: StepStatus = StepStatus.DONE
    latency_ms: int | None = None
    events: list[TurnEvent] = field(default_factory=list)

    def take(self, match: Callable[[TurnEvent], bool]) -> list[TurnEvent]:
        """Remove and return the buffered events that ``match``, so another step writes them."""
        taken = [e for e in self.events if match(e)]
        self.events = [e for e in self.events if not match(e)]
        return taken


class Trail(Protocol):
    async def emit(self, event: TurnEvent) -> None: ...

    def run(self, step: AgentStep) -> AbstractAsyncContextManager[StepRun]: ...

    async def report(
        self,
        step: AgentStep,
        *,
        status: StepStatus,
        started_at: datetime,
        latency_ms: int,
        events: Sequence[TurnEvent] = (),
    ) -> None: ...


class NullTrail:
    """Reports nothing; events go straight to ``sink``. For code run outside a turn."""

    def __init__(self, sink: Callable[[TurnEvent], Awaitable[None]] | None = None) -> None:
        self._sink = sink

    async def emit(self, event: TurnEvent) -> None:
        if self._sink is not None:
            await self._sink(event)

    @asynccontextmanager
    async def run(self, step: AgentStep) -> AsyncIterator[StepRun]:
        run = StepRun(step=step, started_at=utc_now())
        try:
            yield run
        finally:
            for event in run.events:
                await self.emit(event)

    async def report(
        self,
        step: AgentStep,
        *,
        status: StepStatus,
        started_at: datetime,
        latency_ms: int,
        events: Sequence[TurnEvent] = (),
    ) -> None:
        for event in events:
            await self.emit(event)

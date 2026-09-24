"""Tracing port. Every turn is one trace whose id is the turn id (NFR-5.1, NFR-5.2); every
model call is a generation on it. Tracing never breaks a turn: adapters swallow their own
failures, and the glass box keeps working from our own ``model_call`` events (FR-9.2)."""

import uuid
from typing import Protocol

from secondmind.core import ModelCallEvent


class GenerationSpan(Protocol):
    def finish(
        self,
        call: ModelCallEvent,
        *,
        prompt_input: object | None = None,
        output: object | None = None,
    ) -> None: ...

    def fail(self, error: str) -> None: ...


class TurnTrace(Protocol):
    def start_generation(self, step: str) -> GenerationSpan: ...

    def finish(
        self,
        *,
        status: str,
        output: object | None,
        error: str | None,
        redacted_input: object | None = None,
    ) -> None:
        """End the trace. ``redacted_input`` replaces the input when a secret was found late."""
        ...


class Tracer(Protocol):
    @property
    def enabled(self) -> bool: ...

    def start_turn(
        self,
        *,
        turn_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        turn_input: object | None,
        metadata: dict[str, str],
    ) -> TurnTrace: ...

    async def available(self) -> bool:
        """Whether the trace backend is reachable right now (cached briefly)."""
        ...

    def trace_url(self, turn_id: uuid.UUID) -> str | None: ...

    def flush(self) -> None: ...

    def shutdown(self) -> None: ...


def trace_id_for(turn_id: uuid.UUID) -> str:
    """W3C trace id (32 lowercase hex) for a turn: the turn id itself."""
    return turn_id.hex


class _NullGeneration:
    def finish(
        self,
        call: ModelCallEvent,
        *,
        prompt_input: object | None = None,
        output: object | None = None,
    ) -> None:
        return None

    def fail(self, error: str) -> None:
        return None


class _NullTrace:
    def start_generation(self, step: str) -> GenerationSpan:
        return _NullGeneration()

    def finish(
        self,
        *,
        status: str,
        output: object | None,
        error: str | None,
        redacted_input: object | None = None,
    ) -> None:
        return None


class NullTracer:
    """Tracing disabled (no keys, or TRACING_ENABLED=false)."""

    @property
    def enabled(self) -> bool:
        return False

    def start_turn(
        self,
        *,
        turn_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        turn_input: object | None,
        metadata: dict[str, str],
    ) -> TurnTrace:
        return _NullTrace()

    async def available(self) -> bool:
        return False

    def trace_url(self, turn_id: uuid.UUID) -> str | None:
        return None

    def flush(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

"""Langfuse tracer (SDK v4, OpenTelemetry-based).

The trace id is the turn id; each model call is a ``generation`` observation with model,
tokens, cost and latency. Exports run in a background thread, so a Langfuse outage never
blocks or fails a turn; ``available()`` reports reachability for the glass box link.
"""

import time
import uuid
from datetime import timedelta

import httpx
from langfuse import Langfuse

from secondmind.core import ModelCallEvent
from secondmind.observability import GenerationSpan, TurnTrace, get_logger, trace_id_for

log = get_logger(__name__)
_HEALTH_TTL_S = 15.0


class _Generation:
    def __init__(self, observation: object, include_content: bool) -> None:
        self._obs = observation
        self._include_content = include_content
        self._done = False

    def finish(
        self,
        call: ModelCallEvent,
        *,
        prompt_input: object | None = None,
        output: object | None = None,
    ) -> None:
        if self._done:
            return
        self._done = True
        try:
            usage = call.usage
            completion_start = (
                call.started_at + timedelta(milliseconds=call.time_to_first_token_ms)
                if call.time_to_first_token_ms is not None
                else None
            )
            metadata = {
                "provider": call.provider,
                "attempts": str(call.attempts),
                "price_version": usage.price_version,
                "prompt": call.prompt or "",
            }
            if call.fallback is not None:
                metadata["fallback_from"] = (
                    f"{call.fallback.from_provider}:{call.fallback.from_model}"
                )
                metadata["fallback_reason"] = call.fallback.reason[:200]
            self._obs.update(  # type: ignore[attr-defined]
                model=call.model,
                usage_details={
                    "input": usage.input_tokens,
                    "input_cached": usage.cached_input_tokens,
                    "output": usage.output_tokens,
                    "total": usage.total_tokens,
                },
                cost_details={"total": float(usage.cost_usd)},
                completion_start_time=completion_start,
                metadata=metadata,
                input=prompt_input if self._include_content else None,
                output=output if self._include_content else None,
                level="WARNING" if call.fallback else "DEFAULT",
            )
            # The call's own end: generations are finished when the turn ends (content is
            # redacted then), so "now" would be the wrong end time.
            ended = call.started_at + timedelta(milliseconds=call.latency_ms)
            self._obs.end(end_time=int(ended.timestamp() * 1_000_000_000))  # type: ignore[attr-defined]
        except Exception:
            log.warning("trace.generation_failed", exc_info=True)

    def fail(self, error: str) -> None:
        if self._done:
            return
        self._done = True
        try:
            self._obs.update(level="ERROR", status_message=error[:500])  # type: ignore[attr-defined]
            self._obs.end()  # type: ignore[attr-defined]
        except Exception:
            log.warning("trace.generation_failed", exc_info=True)


class _TurnTrace:
    def __init__(self, root: object | None, include_content: bool) -> None:
        self._root = root
        self._include_content = include_content

    def start_generation(self, step: str) -> GenerationSpan:
        if self._root is None:
            return _Generation(_Noop(), self._include_content)
        try:
            obs = self._root.start_observation(name=step, as_type="generation")  # type: ignore[attr-defined]
        except Exception:
            log.warning("trace.generation_start_failed", exc_info=True)
            obs = _Noop()
        return _Generation(obs, self._include_content)

    def finish(
        self,
        *,
        status: str,
        output: object | None,
        error: str | None,
        redacted_input: object | None = None,
    ) -> None:
        if self._root is None:
            return
        try:
            if redacted_input is not None and self._include_content:
                self._root.update(input=redacted_input)  # type: ignore[attr-defined]
            self._root.update(  # type: ignore[attr-defined]
                output=output if self._include_content else None,
                level="ERROR" if error else "DEFAULT",
                status_message=error,
                metadata={"status": status},
            )
            self._root.end()  # type: ignore[attr-defined]
        except Exception:
            log.warning("trace.finish_failed", exc_info=True)


class _Noop:
    def update(self, **_: object) -> "_Noop":
        return self

    def end(self, **_: object) -> "_Noop":
        return self


class LangfuseTracer:
    def __init__(
        self,
        *,
        host: str,
        public_key: str,
        secret_key: str,
        ui_url: str | None,
        project_id: str | None,
        environment: str,
        release: str,
        include_content: bool,
    ) -> None:
        self._host = host.rstrip("/")
        self._ui_url = (ui_url or host).rstrip("/")
        self._project_id = project_id
        self._include_content = include_content
        self._client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=self._host,
            environment=environment,
            release=release,
            flush_interval=1.0,
            timeout=5,
        )
        self._health: tuple[float, bool] | None = None

    @property
    def enabled(self) -> bool:
        return True

    def start_turn(
        self,
        *,
        turn_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        turn_input: object | None,
        metadata: dict[str, str],
    ) -> TurnTrace:
        try:
            root = self._client.start_observation(
                trace_context={"trace_id": trace_id_for(turn_id)},
                name="turn",
                as_type="agent",
                input=turn_input if self._include_content else None,
                metadata={
                    "turn_id": str(turn_id),
                    "workspace_id": str(workspace_id),
                    "user_id": str(user_id),
                    **{k: v[:200] for k, v in metadata.items()},
                },
            )
        except Exception:
            log.warning("trace.start_failed", exc_info=True)
            return _TurnTrace(None, self._include_content)
        return _TurnTrace(root, self._include_content)

    async def available(self) -> bool:
        now = time.monotonic()
        if self._health is not None and now - self._health[0] < _HEALTH_TTL_S:
            return self._health[1]
        ok = False
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{self._host}/api/public/health")
                ok = response.status_code == 200
        except httpx.HTTPError:
            ok = False
        self._health = (now, ok)
        return ok

    def trace_url(self, turn_id: uuid.UUID) -> str | None:
        if not self._project_id:
            return None
        return f"{self._ui_url}/project/{self._project_id}/traces/{trace_id_for(turn_id)}"

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception:
            log.warning("trace.flush_failed", exc_info=True)

    def shutdown(self) -> None:
        try:
            self._client.shutdown()
        except Exception:
            log.warning("trace.shutdown_failed", exc_info=True)

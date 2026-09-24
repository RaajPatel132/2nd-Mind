"""The model router: picks the adapter per step from config and makes every call resilient.

Callers name a *step*, never a provider. For each call the router:

* runs the step's primary model, with a per-call timeout (time to first chunk and between
  chunks when streaming; the whole call otherwise);
* retries retryable errors (timeouts, 429, 5xx, network) with full-jitter backoff, bounded by
  ``max_retries``, and **never after streamed tokens reached the caller**;
* skips a provider whose circuit breaker is open;
* falls back to the step's fallback model when the primary fails or is skipped, marking the
  result ``fallback`` with the reason;
* raises :class:`ProviderUnavailableError` when every option fails;
* returns normalised :class:`Usage` with cost from the price table and measured latency.
"""

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel

from secondmind.config import ModelRef, PriceTable, ResolvedRoute, Routing, Step, StepKind
from secondmind.core import Clock, FallbackInfo, ModelCallEvent, Usage, utc_now
from secondmind.providers.contract import (
    AdapterEmbedding,
    AdapterReply,
    AdapterRequest,
    AdapterStreamEvent,
    AdapterStructured,
    ChatMessage,
    KeepAlive,
    ProviderAdapter,
    RawUsage,
    StreamEnd,
    TextDelta,
    ToolCall,
    ToolSpec,
)
from secondmind.providers.errors import (
    Attempt,
    ProviderError,
    ProviderErrorKind,
    ProviderUnavailableError,
    StreamInterruptedError,
)
from secondmind.providers.resilience import (
    BreakerRegistry,
    CircuitBreaker,
    ResiliencePolicy,
    backoff_delay,
)


@dataclass(frozen=True, slots=True)
class ModelCall:
    """What one routed call cost and who served it."""

    step: str
    provider: str
    model: str
    prompt: str | None
    started_at: datetime
    latency_ms: int
    time_to_first_token_ms: int | None
    attempts: int
    usage: Usage
    fallback: FallbackInfo | None
    cache_hits: int | None = None

    def to_event(self) -> ModelCallEvent:
        return ModelCallEvent(
            step=self.step,
            provider=self.provider,
            model=self.model,
            prompt=self.prompt,
            started_at=self.started_at,
            latency_ms=self.latency_ms,
            time_to_first_token_ms=self.time_to_first_token_ms,
            attempts=self.attempts,
            usage=self.usage,
            fallback=self.fallback,
            cache_hits=self.cache_hits,
        )


@dataclass(frozen=True, slots=True)
class ChatResult:
    text: str
    call: ModelCall
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None


@dataclass(frozen=True, slots=True)
class StructuredResult[T: BaseModel]:
    value: T
    call: ModelCall


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    call: ModelCall


RouterStreamEvent = TextDelta | ChatResult


@dataclass(frozen=True, slots=True)
class _Payload:
    system: str | None
    messages: Sequence[ChatMessage]
    tools: Sequence[ToolSpec] = ()
    cache_prefix: str | None = None


_NO_PAYLOAD = _Payload(system=None, messages=())


@dataclass(slots=True)
class _CallState:
    step: Step
    route: ResolvedRoute
    prompt: str | None
    started_at: datetime
    t0: float
    attempts: list[Attempt] = field(default_factory=list)
    total_attempts: int = 0


class ModelRouter:
    def __init__(
        self,
        *,
        routing: Routing,
        prices: PriceTable,
        adapters: Mapping[str, ProviderAdapter],
        policy: ResiliencePolicy,
        breakers: BreakerRegistry | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        now: Clock = utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._routing = routing
        self._prices = prices
        self._adapters = dict(adapters)
        self._policy = policy
        self._breakers = breakers or BreakerRegistry(policy, monotonic)
        self._monotonic = monotonic
        self._now = now
        self._sleep = sleep
        self._rand = rand
        for ref in routing.refs():
            if ref.provider not in self._adapters:
                raise ValueError(f"no adapter registered for provider {ref.provider!r}")

    @property
    def breakers(self) -> BreakerRegistry:
        return self._breakers

    def route(self, step: Step) -> ResolvedRoute:
        return self._routing.route(step)

    async def aclose(self) -> None:
        for adapter in self._adapters.values():
            await adapter.aclose()

    # ------------------------------------------------------------------ public calls

    async def stream(
        self,
        step: Step,
        *,
        system: str | None,
        messages: Sequence[ChatMessage],
        prompt: str | None = None,
        cache_prefix: str | None = None,
    ) -> AsyncIterator[RouterStreamEvent]:
        """Stream text deltas, then one :class:`ChatResult` with usage."""
        state = self._start(step, StepKind.CHAT, prompt)
        payload = _Payload(system=system, messages=messages, cache_prefix=cache_prefix)
        for index, ref in enumerate(self._candidates(state.route)):
            adapter = self._adapters[ref.provider]
            breaker = self._breakers.get(ref.provider)
            tries = 0
            while breaker.allow():
                tries += 1
                state.total_attempts += 1
                request = self._request(state, ref, payload)
                first_token_at: float | None = None
                settled = False
                reply: AdapterReply | None = None
                try:
                    async for event in self._idle_timeout(
                        adapter.stream_chat(request), state.route.timeout_s, ref
                    ):
                        if isinstance(event, StreamEnd):
                            reply = event.reply
                        elif event.text:
                            if first_token_at is None:
                                first_token_at = self._monotonic()
                            yield event
                    if reply is None:
                        raise ProviderError(
                            ProviderErrorKind.INVALID_OUTPUT,
                            "stream ended without a final message",
                            provider=ref.provider,
                        )
                except ProviderError as err:
                    settled = True
                    if first_token_at is not None:
                        self._fail(state, breaker, ref, err)
                        raise StreamInterruptedError(step.value, state.attempts) from err
                    if await self._retry_after(state, breaker, ref, err, tries):
                        continue
                    break
                finally:
                    if not settled and reply is None:
                        breaker.release_probe()
                breaker.record_success()
                ttft = None if first_token_at is None else self._ms_since(state.t0, first_token_at)
                call = self._finish(state, ref, reply.usage, index, ttft)
                yield ChatResult(
                    text=reply.text,
                    call=call,
                    tool_calls=list(reply.tool_calls),
                    stop_reason=reply.stop_reason,
                )
                return
            else:
                state.attempts.append(Attempt(ref.provider, ref.model, "skipped: breaker open"))
        raise ProviderUnavailableError(step.value, state.attempts)

    async def chat(
        self,
        step: Step,
        *,
        system: str | None,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        prompt: str | None = None,
    ) -> ChatResult:
        """Non-streaming chat, optionally with tools the model may call."""

        async def invoke(adapter: ProviderAdapter, request: AdapterRequest) -> AdapterReply:
            return await adapter.chat(request)

        state = self._start(step, StepKind.CHAT, prompt)
        payload = _Payload(system=system, messages=messages, tools=tools)
        reply, call = await self._invoke(state, payload, invoke, _reply_usage)
        return ChatResult(
            text=reply.text,
            call=call,
            tool_calls=list(reply.tool_calls),
            stop_reason=reply.stop_reason,
        )

    async def structured[T: BaseModel](
        self,
        step: Step,
        schema: type[T],
        *,
        system: str | None,
        messages: Sequence[ChatMessage],
        prompt: str | None = None,
        cache_prefix: str | None = None,
    ) -> StructuredResult[T]:
        """Structured output: a Pydantic model in, a validated instance out."""

        async def invoke(adapter: ProviderAdapter, request: AdapterRequest) -> AdapterStructured[T]:
            return await adapter.structured(request, schema)

        state = self._start(step, StepKind.CHAT, prompt)
        payload = _Payload(system=system, messages=messages, cache_prefix=cache_prefix)
        result, call = await self._invoke(state, payload, invoke, _structured_usage)
        return StructuredResult(value=result.value, call=call)

    async def embed(
        self,
        texts: Sequence[str],
        *,
        step: Step = Step.EMBED,
        dimensions: int | None = None,
    ) -> EmbeddingResult:
        async def invoke(adapter: ProviderAdapter, request: AdapterRequest) -> AdapterEmbedding:
            result = await adapter.embed(request.model, texts, dimensions)
            if dimensions is not None and any(len(v) != dimensions for v in result.vectors):
                raise ProviderError(
                    ProviderErrorKind.INVALID_OUTPUT,
                    f"embeddings are not {dimensions}-dimensional",
                    provider=adapter.name,
                )
            return result

        state = self._start(step, StepKind.EMBEDDING, None)
        result, call = await self._invoke(state, _NO_PAYLOAD, invoke, _embedding_usage)
        return EmbeddingResult(vectors=result.vectors, call=call)

    # ------------------------------------------------------------------ internals

    async def _invoke[R](
        self,
        state: _CallState,
        payload: _Payload,
        invoke: Callable[[ProviderAdapter, AdapterRequest], Awaitable[R]],
        usage_of: Callable[[R], RawUsage],
    ) -> tuple[R, ModelCall]:
        for index, ref in enumerate(self._candidates(state.route)):
            adapter = self._adapters[ref.provider]
            breaker = self._breakers.get(ref.provider)
            tries = 0
            while breaker.allow():
                tries += 1
                state.total_attempts += 1
                request = self._request(state, ref, payload)
                settled = False
                try:
                    async with asyncio.timeout(state.route.timeout_s):
                        result = await invoke(adapter, request)
                    settled = True
                except TimeoutError:
                    settled = True
                    err = _timeout(ref, state.route.timeout_s)
                except ProviderError as exc:
                    settled = True
                    err = exc
                else:
                    breaker.record_success()
                    return result, self._finish(state, ref, usage_of(result), index, None)
                finally:
                    if not settled:
                        breaker.release_probe()
                if await self._retry_after(state, breaker, ref, err, tries):
                    continue
                break
            else:
                state.attempts.append(Attempt(ref.provider, ref.model, "skipped: breaker open"))
        raise ProviderUnavailableError(state.step.value, state.attempts)

    def _start(self, step: Step, kind: StepKind, prompt: str | None) -> _CallState:
        route = self._routing.route(step)
        if route.kind is not kind:
            raise ValueError(f"step {step.value!r} is a {route.kind.value} step, not {kind.value}")
        return _CallState(
            step=step,
            route=route,
            prompt=prompt or route.prompt,
            started_at=self._now(),
            t0=self._monotonic(),
        )

    @staticmethod
    def _candidates(route: ResolvedRoute) -> list[ModelRef]:
        return [route.primary] + ([route.fallback] if route.fallback else [])

    @staticmethod
    def _request(state: _CallState, ref: ModelRef, payload: _Payload) -> AdapterRequest:
        return AdapterRequest(
            step=state.step.value,
            model=ref.model,
            system=payload.system,
            messages=payload.messages,
            max_output_tokens=state.route.max_output_tokens,
            effort=state.route.effort,
            tools=payload.tools,
            cache_prefix=payload.cache_prefix,
        )

    def _fail(
        self, state: _CallState, breaker: CircuitBreaker, ref: ModelRef, err: ProviderError
    ) -> None:
        # Only transient failures count against provider health; a 400 means it answered.
        if err.retryable:
            breaker.record_failure()
        else:
            breaker.record_success()
        state.attempts.append(_attempt(ref, err))

    async def _retry_after(
        self,
        state: _CallState,
        breaker: CircuitBreaker,
        ref: ModelRef,
        err: ProviderError,
        tries: int,
    ) -> bool:
        """Record the failure; back off and return True if this attempt should be retried."""
        self._fail(state, breaker, ref, err)
        if err.retryable and tries <= self._policy.max_retries:
            await self._sleep(backoff_delay(tries, self._policy, self._rand))
            return True
        return False

    def _finish(
        self, state: _CallState, ref: ModelRef, raw: RawUsage, index: int, ttft_ms: int | None
    ) -> ModelCall:
        latency_ms = self._ms_since(state.t0, self._monotonic())
        usage = Usage(
            provider=ref.provider,
            model=ref.model,
            input_tokens=raw.input_tokens,
            cached_input_tokens=raw.cached_input_tokens,
            output_tokens=raw.output_tokens,
            cost_usd=self._prices.cost(
                ref,
                input_tokens=raw.input_tokens,
                cached_input_tokens=raw.cached_input_tokens,
                output_tokens=raw.output_tokens,
            ),
            latency_ms=latency_ms,
            price_version=self._prices.version,
        )
        fallback = None
        if index > 0:
            primary = state.route.primary
            fallback = FallbackInfo(
                from_provider=primary.provider,
                from_model=primary.model,
                reason="; ".join(
                    a.outcome for a in state.attempts if a.provider == primary.provider
                )
                or "primary unavailable",
            )
        return ModelCall(
            step=state.step.value,
            provider=ref.provider,
            model=ref.model,
            prompt=state.prompt,
            started_at=state.started_at,
            latency_ms=latency_ms,
            time_to_first_token_ms=ttft_ms,
            attempts=state.total_attempts,
            usage=usage,
            fallback=fallback,
        )

    @staticmethod
    def _ms_since(start: float, end: float) -> int:
        return max(0, round((end - start) * 1000))

    @staticmethod
    async def _idle_timeout(
        events: AsyncIterator[AdapterStreamEvent], timeout_s: float, ref: ModelRef
    ) -> AsyncIterator[TextDelta | StreamEnd]:
        """Yield stream events, failing if the first or any next event takes > timeout_s."""
        iterator = aiter(events)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(anext(iterator), timeout_s)
                except StopAsyncIteration:
                    return
                except TimeoutError as exc:
                    raise _timeout(ref, timeout_s) from exc
                if not isinstance(event, KeepAlive):
                    yield event
        finally:
            closer = getattr(iterator, "aclose", None)
            if closer is not None:
                await closer()


def _timeout(ref: ModelRef, timeout_s: float) -> ProviderError:
    return ProviderError(
        ProviderErrorKind.TIMEOUT, f"no response within {timeout_s:g}s", provider=ref.provider
    )


def _attempt(ref: ModelRef, err: ProviderError) -> Attempt:
    status = f" {err.status_code}" if err.status_code else ""
    return Attempt(ref.provider, ref.model, f"{err.kind.value}{status}")


def _reply_usage(reply: AdapterReply) -> RawUsage:
    return reply.usage


def _structured_usage(result: AdapterStructured[BaseModel]) -> RawUsage:
    return result.usage


def _embedding_usage(result: AdapterEmbedding) -> RawUsage:
    return result.usage

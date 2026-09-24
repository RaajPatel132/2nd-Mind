"""S1.8: timeouts, bounded jittered retries, circuit breaker, fallback, clean failure."""

import pytest

from secondmind.config import Step
from secondmind.providers import (
    BreakerState,
    ChatMessage,
    ChatResult,
    FakeOutcome,
    ProviderErrorKind,
    ProviderUnavailableError,
    ResiliencePolicy,
    StreamInterruptedError,
    TextDelta,
    backoff_delay,
)
from tests.unit.providers.helpers import Clock, Sleeps, fakes, router

HELLO = [ChatMessage.user("hello")]
RATE_LIMITED = FakeOutcome(error=ProviderErrorKind.RATE_LIMITED)


async def collect(r, **kw) -> tuple[str, ChatResult]:  # type: ignore[no-untyped-def]
    text, final = "", None
    async for event in r.stream(Step.ANSWER, system=None, messages=HELLO, **kw):
        if isinstance(event, TextDelta):
            text += event.text
        else:
            final = event
    assert final is not None
    return text, final


async def test_retry_then_succeed() -> None:
    adapters = fakes("primary")
    adapters["primary"].script.add(RATE_LIMITED, RATE_LIMITED, FakeOutcome(text="ok now"))
    sleeps = Sleeps()
    r = router(adapters, "primary:m", sleep=sleeps)

    text, result = await collect(r)

    assert text == "ok now"
    assert result.call.attempts == 3
    assert result.call.fallback is None
    assert len(sleeps.calls) == 2
    assert sleeps.calls[0] < sleeps.calls[1]  # exponential with fixed jitter


async def test_retries_exhausted_then_fallback_is_marked() -> None:
    adapters = fakes("primary", "backup")
    adapters["primary"].script.add(RATE_LIMITED)
    adapters["backup"].script.add(FakeOutcome(text="from backup"))
    r = router(adapters, "primary:m", "backup:m", policy=ResiliencePolicy(max_retries=2))

    text, result = await collect(r)

    assert text == "from backup"
    assert len(adapters["primary"].requests) == 3  # 1 + 2 retries
    assert result.call.provider == "backup"
    assert result.call.fallback is not None
    assert result.call.fallback.from_provider == "primary"
    assert "rate_limited 429" in result.call.fallback.reason
    event = result.call.to_event()
    assert event.fallback == result.call.fallback


async def test_non_retryable_error_is_not_retried_but_falls_back() -> None:
    adapters = fakes("primary", "backup")
    adapters["primary"].script.add(FakeOutcome(error=ProviderErrorKind.BAD_REQUEST))
    r = router(adapters, "primary:m", "backup:m")

    _, result = await collect(r)

    assert len(adapters["primary"].requests) == 1
    assert result.call.provider == "backup"


async def test_breaker_opens_and_skips_the_provider() -> None:
    adapters = fakes("primary", "backup")
    adapters["primary"].script.add(FakeOutcome(error=ProviderErrorKind.SERVER))
    clock = Clock()
    policy = ResiliencePolicy(max_retries=0, breaker_threshold=2, breaker_cooldown_s=30)
    r = router(adapters, "primary:m", "backup:m", policy=policy, clock=clock)

    await collect(r)
    await collect(r)
    assert r.breakers.get("primary").state is BreakerState.OPEN
    calls_before = len(adapters["primary"].requests)

    _, result = await collect(r)

    assert len(adapters["primary"].requests) == calls_before  # skipped, not called
    assert result.call.fallback is not None
    assert "skipped: breaker open" in result.call.fallback.reason


async def test_breaker_half_opens_after_cooldown_and_recovers() -> None:
    adapters = fakes("primary")
    adapters["primary"].script.add(
        FakeOutcome(error=ProviderErrorKind.SERVER), FakeOutcome(text="recovered")
    )
    clock = Clock()
    policy = ResiliencePolicy(max_retries=0, breaker_threshold=1, breaker_cooldown_s=30)
    r = router(adapters, "primary:m", policy=policy, clock=clock)

    with pytest.raises(ProviderUnavailableError):
        await collect(r)
    assert r.breakers.get("primary").state is BreakerState.OPEN
    with pytest.raises(ProviderUnavailableError) as skipped:
        await collect(r)
    assert skipped.value.attempts[-1].outcome == "skipped: breaker open"

    clock.t += 31
    assert r.breakers.get("primary").state is BreakerState.HALF_OPEN
    text, _ = await collect(r)
    assert text == "recovered"
    assert r.breakers.get("primary").state is BreakerState.CLOSED


async def test_no_retry_after_streaming_started() -> None:
    adapters = fakes("primary", "backup")
    adapters["primary"].script.add(
        FakeOutcome(
            text="one two three four", fail_after_tokens=2, error=ProviderErrorKind.CONNECTION
        )
    )
    r = router(adapters, "primary:m", "backup:m")
    seen: list[str] = []

    async def consume() -> None:
        async for event in r.stream(Step.ANSWER, system=None, messages=HELLO):
            if isinstance(event, TextDelta):
                seen.append(event.text)  # noqa: PERF401 - must record before the failure

    with pytest.raises(StreamInterruptedError) as exc:
        await consume()

    assert seen == ["one ", "two "]
    assert len(adapters["primary"].requests) == 1  # no retry
    assert adapters["backup"].requests == []  # no fallback either
    assert exc.value.code == "provider_stream_interrupted"


async def test_timeout_is_retryable() -> None:
    adapters = fakes("primary")
    adapters["primary"].script.add(FakeOutcome(delay_s=0.2), FakeOutcome(text="fast"))
    r = router(adapters, "primary:m", timeout_s=0.05)

    text, result = await collect(r)

    assert text == "fast"
    assert result.call.attempts == 2


async def test_every_option_failing_raises_a_typed_user_safe_error() -> None:
    adapters = fakes("primary", "backup")
    for fake in adapters.values():
        fake.script.add(FakeOutcome(error=ProviderErrorKind.SERVER))
    r = router(adapters, "primary:m", "backup:m", policy=ResiliencePolicy(max_retries=1))

    with pytest.raises(ProviderUnavailableError) as exc:
        await collect(r)

    err = exc.value
    assert err.code == "provider_unavailable"
    assert (
        err.message == "The model provider is unavailable right now. Please try again in a moment."
    )
    assert [a.provider for a in err.attempts] == ["primary", "primary", "backup", "backup"]
    assert "server 500" in err.detail


async def test_non_streaming_calls_use_the_same_resilience() -> None:
    adapters = fakes("primary", "backup")
    adapters["primary"].script.add(FakeOutcome(error=ProviderErrorKind.TIMEOUT))
    adapters["backup"].script.add(FakeOutcome(text="hi"))
    r = router(adapters, "primary:m", "backup:m", policy=ResiliencePolicy(max_retries=1))

    result = await r.chat(Step.ANSWER, system=None, messages=HELLO)

    assert result.text == "hi"
    assert len(adapters["primary"].requests) == 2
    assert result.call.fallback is not None


def test_backoff_is_bounded_and_jittered() -> None:
    policy = ResiliencePolicy(base_delay_s=0.25, max_delay_s=1.0)
    assert backoff_delay(1, policy, lambda: 1.0) == 0.25
    assert backoff_delay(2, policy, lambda: 1.0) == 0.5
    assert backoff_delay(10, policy, lambda: 1.0) == 1.0  # capped
    assert backoff_delay(3, policy, lambda: 0.0) == 0.0  # full jitter lower bound

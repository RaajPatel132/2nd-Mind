"""Retries with jittered backoff and a circuit breaker per provider (FR-14.4)."""

import random
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class ResiliencePolicy:
    max_retries: int = 2
    base_delay_s: float = 0.25
    max_delay_s: float = 4.0
    breaker_threshold: int = 5
    breaker_window_s: float = 60.0
    breaker_cooldown_s: float = 30.0


def backoff_delay(
    attempt: int, policy: ResiliencePolicy, rand: Callable[[], float] = random.random
) -> float:
    """Full-jitter exponential backoff: uniform in [0, min(cap, base * 2^(attempt-1))]."""
    ceiling = min(policy.max_delay_s, policy.base_delay_s * (2.0 ** max(0, attempt - 1)))
    return rand() * ceiling


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Opens after ``threshold`` failures within ``window_s``; after ``cooldown_s`` lets a
    single probe through (half-open). The probe's outcome closes or re-opens it."""

    def __init__(
        self,
        *,
        threshold: int,
        window_s: float,
        cooldown_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = threshold
        self._window_s = window_s
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._failures: deque[float] = deque()
        self._opened_at: float | None = None
        self._probe_in_flight = False

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return BreakerState.CLOSED
        if self._clock() - self._opened_at >= self._cooldown_s:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    def allow(self) -> bool:
        state = self.state
        if state is BreakerState.CLOSED:
            return True
        if state is BreakerState.HALF_OPEN and not self._probe_in_flight:
            self._probe_in_flight = True
            return True
        return False

    def release_probe(self) -> None:
        """Free a half-open probe slot whose call ended without an outcome (e.g. cancelled)."""
        self._probe_in_flight = False

    def record_success(self) -> None:
        self._failures.clear()
        self._opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> None:
        now = self._clock()
        if self._opened_at is not None:
            # A failed half-open probe re-opens for another full cool-down.
            self._opened_at = now
            self._probe_in_flight = False
            return
        self._failures.append(now)
        while self._failures and now - self._failures[0] > self._window_s:
            self._failures.popleft()
        if len(self._failures) >= self._threshold:
            self._opened_at = now
            self._failures.clear()


class BreakerRegistry:
    """One breaker per provider name, shared by every step routed to that provider."""

    def __init__(
        self, policy: ResiliencePolicy, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._policy = policy
        self._clock = clock
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, provider: str) -> CircuitBreaker:
        breaker = self._breakers.get(provider)
        if breaker is None:
            breaker = CircuitBreaker(
                threshold=self._policy.breaker_threshold,
                window_s=self._policy.breaker_window_s,
                cooldown_s=self._policy.breaker_cooldown_s,
                clock=self._clock,
            )
            self._breakers[provider] = breaker
        return breaker

    def states(self) -> dict[str, BreakerState]:
        return {name: b.state for name, b in sorted(self._breakers.items())}

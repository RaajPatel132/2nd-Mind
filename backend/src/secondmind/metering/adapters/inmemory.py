"""The spend store without Redis (tests and offline runs): the same behaviour, in a process."""

import time
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from secondmind.metering.gate import SpendTotals, utc_day, utc_month


class InMemorySpendStore:
    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._kill: bool | None = None
        self._day: dict[str, Decimal] = {}
        self._month: dict[str, Decimal] = {}
        self._providers: dict[str, Decimal] = {}
        self._credit_out: dict[str, float] = {}
        self._seen: dict[str, float] = {}
        self._buckets: dict[str, tuple[float, float]] = {}
        self.fail = False  # a test can make every read raise, as an unreachable Redis would

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("spend store is down")

    async def kill_switch(self) -> bool | None:
        self._check()
        return self._kill

    async def set_kill_switch(self, on: bool) -> None:
        self._kill = on

    async def add(self, provider: str, cost: Decimal, at: datetime) -> SpendTotals:
        self._check()
        day, month = utc_day(at), utc_month(at)
        self._day[day] = self._day.get(day, Decimal(0)) + cost
        self._month[month] = self._month.get(month, Decimal(0)) + cost
        self._providers[provider] = self._providers.get(provider, Decimal(0)) + cost
        return await self.totals(at)

    async def totals(self, at: datetime) -> SpendTotals:
        self._check()
        return SpendTotals(
            day=self._day.get(utc_day(at), Decimal(0)),
            month=self._month.get(utc_month(at), Decimal(0)),
            providers=dict(self._providers),
        )

    async def reset(self, totals: SpendTotals, at: datetime) -> None:
        self._day[utc_day(at)] = totals.day
        self._month[utc_month(at)] = totals.month
        self._providers = dict(totals.providers)

    async def credit_out(self, provider: str) -> bool:
        self._check()
        return self._credit_out.get(provider, 0.0) > self._monotonic()

    async def mark_credit_out(self, provider: str, seconds: int) -> None:
        self._credit_out[provider] = self._monotonic() + seconds

    async def once(self, key: str, seconds: int) -> bool:
        now = self._monotonic()
        if self._seen.get(key, 0.0) > now:
            return False
        self._seen[key] = now + seconds
        return True

    async def take(self, bucket: str, per_minute: int) -> float | None:
        now = self._monotonic()
        tokens, at = self._buckets.get(bucket, (float(per_minute), now))
        tokens = min(float(per_minute), tokens + (now - at) * per_minute / 60)
        if tokens >= 1:
            self._buckets[bucket] = (tokens - 1, now)
            return None
        self._buckets[bucket] = (tokens, now)
        return (1 - tokens) * 60 / per_minute

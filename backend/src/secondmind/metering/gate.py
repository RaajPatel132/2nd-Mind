"""The spend gate (R.10, ADR-0032): what may spend money, checked before a turn starts and on
every model call that isn't part of an admitted turn.

In order, the first that applies blocks: the kill switch, the daily and monthly spend caps,
both providers' credit used up, and the person's own quota. A provider whose credit is used up
(by our own count, or because it said so) is skipped for its steps' fallbacks. Spend counters
live in the :class:`SpendStore` (Redis): every priced call adds to them, and a periodic job
resets them from the usage ledger, which stays the source of truth.
"""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from datetime import time as clock_time
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from secondmind.core import Clock, usd, utc_now
from secondmind.metering.quota import QuotaUsage
from secondmind.observability import get_logger

log = get_logger(__name__)

# How long a process trusts its last read of the kill switch.
FLAG_TTL_S = 2.0
# How long a provider that said "out of credit" is skipped before it's tried again.
CREDIT_OUT_S = 3_600


class BlockReason(StrEnum):
    KILL_SWITCH = "kill_switch"
    DAILY_CAP = "daily_cap"
    MONTHLY_CAP = "monthly_cap"
    PROVIDER_CREDIT = "provider_credit"
    QUOTA = "quota"
    UNAVAILABLE = "spend_check_unavailable"


_MESSAGES = {
    BlockReason.KILL_SWITCH: "New answers are paused for everyone right now.",
    BlockReason.DAILY_CAP: "Today's spending limit for the whole app is used up.",
    BlockReason.MONTHLY_CAP: "This month's spending limit for the whole app is used up.",
    BlockReason.PROVIDER_CREDIT: "The model providers' credit for this app is used up.",
    BlockReason.QUOTA: "You've used your whole allowance.",
    BlockReason.UNAVAILABLE: "Spending can't be checked right now, so new answers are paused.",
}

STILL_WORKS = "You can still browse your memory, check Upcoming, undo, and open the glass box."


@dataclass(frozen=True, slots=True)
class Block:
    reason: BlockReason
    limit: Decimal | None = None
    value: Decimal | None = None

    @property
    def message(self) -> str:
        return _MESSAGES[self.reason]

    @property
    def reply(self) -> str:
        """The template reply of a blocked turn: why, and what still works."""
        return f"{self.message} {STILL_WORKS}"


@dataclass(frozen=True, slots=True)
class SpendLimits:
    daily_usd: Decimal
    monthly_usd: Decimal
    warn_ratio: float = 0.8
    credits_usd: Mapping[str, Decimal] = field(default_factory=dict)
    credit_since: date | None = None
    kill_switch: bool = False
    # The real providers the routing calls: when every one is out of credit, turns stop.
    providers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SpendTotals:
    day: Decimal = Decimal(0)
    month: Decimal = Decimal(0)
    # Per provider, since the credit's start date (or ever, when none is set).
    providers: Mapping[str, Decimal] = field(default_factory=dict)


class SpendStore(Protocol):
    """Runtime spend state shared by every process (Redis)."""

    async def kill_switch(self) -> bool | None:
        """The runtime flag; None when it was never set (the env value applies)."""
        ...

    async def set_kill_switch(self, on: bool) -> None: ...

    async def add(self, provider: str, cost: Decimal, at: datetime) -> SpendTotals:
        """Add a call's cost to the day, month and provider counters; the new totals."""
        ...

    async def totals(self, at: datetime) -> SpendTotals: ...

    async def reset(self, totals: SpendTotals, at: datetime) -> None:
        """Set the counters to ``totals`` (from the ledger)."""
        ...

    async def credit_out(self, provider: str) -> bool: ...

    async def mark_credit_out(self, provider: str, seconds: int) -> None: ...

    async def once(self, key: str, seconds: int) -> bool:
        """True the first time ``key`` is seen within ``seconds``."""
        ...

    async def take(self, bucket: str, per_minute: int) -> float | None:
        """Take a token from a per-minute bucket: None if allowed, else seconds to wait."""
        ...


class SpendReader(Protocol):
    """The ledger's totals since a moment, per provider (across every workspace)."""

    async def since(self, moment: datetime) -> Mapping[str, Decimal]: ...


class SpendGate:
    """Implements the router's ``CallGuard`` and answers whether a turn may start."""

    def __init__(
        self,
        store: SpendStore,
        limits: SpendLimits,
        *,
        clock: Clock = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._limits = limits
        self._clock = clock
        self._monotonic = monotonic
        self._flag: tuple[float, bool] | None = None

    @property
    def limits(self) -> SpendLimits:
        return self._limits

    @property
    def store(self) -> SpendStore:
        return self._store

    async def kill_switch_on(self) -> bool:
        now = self._monotonic()
        if self._flag is not None and now - self._flag[0] < FLAG_TTL_S:
            return self._flag[1]
        flag = await self._store.kill_switch()
        on = self._limits.kill_switch if flag is None else flag
        self._flag = (now, on)
        return on

    async def app_block(self) -> Block | None:
        """What stops every new turn, if anything. Fails closed if the store can't be read."""
        limits = self._limits
        providers = limits.providers or tuple(limits.credits_usd)
        try:
            if await self.kill_switch_on():
                return Block(BlockReason.KILL_SWITCH)
            totals = await self._store.totals(self._clock())
            out = [p for p in providers if await self._credit_used_up(p, totals)]
        except Exception:
            log.exception("spend.check_failed")
            return Block(BlockReason.UNAVAILABLE)
        if totals.day >= limits.daily_usd:
            return Block(BlockReason.DAILY_CAP, limits.daily_usd, totals.day)
        if totals.month >= limits.monthly_usd:
            return Block(BlockReason.MONTHLY_CAP, limits.monthly_usd, totals.month)
        if providers and len(out) == len(providers):
            used = usd(sum((totals.providers.get(p, Decimal(0)) for p in out), Decimal(0)))
            limit = (
                usd(sum(limits.credits_usd.values(), Decimal(0))) if limits.credits_usd else None
            )
            return Block(BlockReason.PROVIDER_CREDIT, limit, used)
        return None

    async def turn_block(self, quota: QuotaUsage | None) -> Block | None:
        """What stops this person's next turn: the app's blocks, then their quota."""
        block = await self.app_block()
        if block is not None:
            return block
        if quota is not None and quota.remaining_usd <= 0:
            return Block(BlockReason.QUOTA, quota.limit_usd, quota.used_usd)
        return None

    async def rate_limited(self, identity: str, per_minute: int) -> float | None:
        """Seconds to wait before this identity may start another turn, or None."""
        return await self._store.take(f"turns:{identity}", per_minute)

    async def reconcile(self, reader: SpendReader) -> SpendTotals:
        """Reset the spend counters to what the usage ledger says (the ledger is the source of
        truth; the counters drift when a process dies between a call and its count)."""
        at = self._clock()
        local = at.astimezone(UTC)
        day_start = datetime.combine(local.date(), clock_time.min, tzinfo=UTC)
        month_start = day_start.replace(day=1)
        credit_start = (
            datetime.combine(self._limits.credit_since, clock_time.min, tzinfo=UTC)
            if self._limits.credit_since is not None
            else datetime(2000, 1, 1, tzinfo=UTC)
        )
        totals = SpendTotals(
            day=usd(sum((await reader.since(day_start)).values(), Decimal(0))),
            month=usd(sum((await reader.since(month_start)).values(), Decimal(0))),
            providers={k: usd(v) for k, v in (await reader.since(credit_start)).items()},
        )
        await self._store.reset(totals, at)
        return totals

    # ------------------------------------------------------------------ CallGuard

    async def refuse(self) -> str | None:
        block = await self.app_block()
        return None if block is None else block.reason.value

    async def skip_provider(self, provider: str) -> bool:
        try:
            totals = await self._store.totals(self._clock())
            return await self._credit_used_up(provider, totals)
        except Exception:
            log.exception("spend.credit_check_failed", provider=provider)
            return False

    async def spent(self, provider: str, cost: Decimal) -> None:
        at = self._clock()
        try:
            totals = await self._store.add(provider, cost, at)
        except Exception:
            # The reconcile job restores the counters from the ledger.
            log.exception("spend.count_failed", provider=provider)
            return
        await self._warn(totals, at)

    async def out_of_credit(self, provider: str) -> None:
        log.warning("spend.provider_out_of_credit", provider=provider)
        try:
            await self._store.mark_credit_out(provider, CREDIT_OUT_S)
        except Exception:
            log.exception("spend.credit_mark_failed", provider=provider)

    # ------------------------------------------------------------------ internals

    async def _credit_used_up(self, provider: str, totals: SpendTotals) -> bool:
        if await self._store.credit_out(provider):
            return True
        limit = self._limits.credits_usd.get(provider)
        return limit is not None and totals.providers.get(provider, Decimal(0)) >= limit

    async def _warn(self, totals: SpendTotals, at: datetime) -> None:
        ratio = Decimal(str(self._limits.warn_ratio))
        checks = (
            ("daily", totals.day, self._limits.daily_usd, at.strftime("%Y-%m-%d"), 2 * 86_400),
            ("monthly", totals.month, self._limits.monthly_usd, at.strftime("%Y-%m"), 40 * 86_400),
        )
        for name, value, limit, period, ttl in checks:
            if (
                limit > 0
                and value >= limit * ratio
                and await self._store.once(f"warn:{name}:{period}", ttl)
            ):
                log.warning(
                    "spend.cap_warning",
                    cap=name,
                    period=period,
                    spent_usd=float(value),
                    limit_usd=float(limit),
                    ratio=float(value / limit),
                )


def utc_day(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%d")


def utc_month(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m")

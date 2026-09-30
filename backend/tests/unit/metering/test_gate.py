"""R.10 / ADR-0032: the spend gate. Each block, in order; the flag reaching every process; credit
per provider; counters that agree with the ledger; and a store that can't be read fails closed."""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from structlog.testing import capture_logs

from secondmind.core import Tier, usd
from secondmind.metering import (
    BlockReason,
    QuotaUsage,
    SpendGate,
    SpendLimits,
    SpendTotals,
)
from secondmind.metering.adapters import InMemorySpendStore

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


class Ticker:
    """A monotonic clock a test moves by hand."""

    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def limits(**changes: object) -> SpendLimits:
    base: dict[str, object] = {
        "daily_usd": Decimal("0.50"),
        "monthly_usd": Decimal(5),
        "providers": ("anthropic", "openai"),
    }
    return SpendLimits(**(base | changes))  # type: ignore[arg-type]


def gate_over(
    store: InMemorySpendStore | None = None, ticker: Ticker | None = None, **changes: object
) -> tuple[SpendGate, InMemorySpendStore, Ticker]:
    ticker = ticker or Ticker()
    store = store or InMemorySpendStore(ticker)
    return SpendGate(store, limits(**changes), clock=lambda: NOW, monotonic=ticker), store, ticker


def quota(remaining: str, limit: str = "2.50") -> QuotaUsage:
    return QuotaUsage(
        tier=Tier.STANDARD,
        limit_usd=usd(limit),
        used_usd=usd(Decimal(limit) - Decimal(remaining)),
        remaining_usd=usd(remaining),
        used_tokens=0,
    )


async def test_nothing_blocks_a_fresh_app() -> None:
    gate, _, _ = gate_over()
    assert await gate.app_block() is None
    assert await gate.turn_block(quota("2.50")) is None
    assert await gate.refuse() is None


async def test_the_kill_switch_blocks_and_the_flag_overrides_the_environment() -> None:
    gate, store, ticker = gate_over(kill_switch=True)
    block = await gate.app_block()
    assert block is not None
    assert block.reason is BlockReason.KILL_SWITCH
    assert await gate.refuse() == "kill_switch"

    await store.set_kill_switch(False)  # `make kill-switch off`, whatever KILL_SWITCH said
    assert await gate.app_block() is not None  # still cached: a process re-reads within 2 s
    ticker.t += 2.1
    assert await gate.app_block() is None
    await store.set_kill_switch(True)
    ticker.t += 2.1
    block = await gate.app_block()
    assert block is not None
    assert block.reason is BlockReason.KILL_SWITCH


async def test_the_daily_cap_blocks_at_the_cap_not_below() -> None:
    gate, _, _ = gate_over()
    await gate.spent("anthropic", Decimal("0.49"))
    assert await gate.app_block() is None
    await gate.spent("openai", Decimal("0.01"))
    block = await gate.app_block()
    assert block is not None
    assert block.reason is BlockReason.DAILY_CAP
    assert (block.limit, block.value) == (Decimal("0.50"), Decimal("0.50"))


async def test_the_monthly_cap_blocks_when_the_day_is_fine() -> None:
    gate, store, _ = gate_over()
    await store.reset(SpendTotals(day=Decimal("0.10"), month=Decimal("5.00")), NOW)
    block = await gate.app_block()
    assert block is not None
    assert block.reason is BlockReason.MONTHLY_CAP


async def test_one_provider_out_of_credit_is_skipped_and_both_block_the_app() -> None:
    gate, store, _ = gate_over(
        credits_usd={"anthropic": Decimal(4), "openai": Decimal(2)},
        credit_since=date(2026, 9, 1),
        daily_usd=Decimal(100),
        monthly_usd=Decimal(100),
    )
    await store.reset(
        SpendTotals(providers={"anthropic": Decimal("4.00"), "openai": Decimal("0.10")}), NOW
    )
    assert await gate.skip_provider("anthropic") is True
    assert await gate.skip_provider("openai") is False
    assert await gate.app_block() is None  # its steps go to the fallback on the other one

    await store.reset(
        SpendTotals(providers={"anthropic": Decimal("4.00"), "openai": Decimal("2.00")}), NOW
    )
    block = await gate.app_block()
    assert block is not None
    assert block.reason is BlockReason.PROVIDER_CREDIT


async def test_a_providers_own_out_of_credit_error_counts_like_our_count() -> None:
    gate, store, _ = gate_over()
    assert await gate.skip_provider("anthropic") is False
    await gate.out_of_credit("anthropic")  # the provider answered "credit balance is too low"
    assert await gate.skip_provider("anthropic") is True
    assert await gate.skip_provider("openai") is False
    assert await gate.app_block() is None
    await gate.out_of_credit("openai")
    block = await gate.app_block()
    assert block is not None
    assert block.reason is BlockReason.PROVIDER_CREDIT
    assert await store.credit_out("anthropic")


async def test_the_quota_blocks_a_person_last_and_only_them() -> None:
    gate, _, _ = gate_over()
    assert await gate.turn_block(quota("0.01")) is None
    block = await gate.turn_block(quota("0.00"))
    assert block is not None
    assert block.reason is BlockReason.QUOTA
    assert (block.limit, block.value) == (Decimal("2.50"), Decimal("2.50"))
    assert await gate.app_block() is None  # someone else's turn is fine


async def test_the_app_blocks_come_before_the_quota_and_in_order() -> None:
    gate, store, _ = gate_over(kill_switch=True)
    await store.reset(SpendTotals(day=Decimal(1), month=Decimal(9)), NOW)
    block = await gate.turn_block(quota("0.00"))
    assert block is not None
    assert block.reason is BlockReason.KILL_SWITCH
    await store.set_kill_switch(False)
    gate2, _, _ = gate_over(store)
    block = await gate2.turn_block(quota("0.00"))
    assert block is not None
    assert block.reason is BlockReason.DAILY_CAP


async def test_a_store_that_cant_be_read_fails_closed() -> None:
    gate, store, _ = gate_over()
    store.fail = True
    block = await gate.app_block()
    assert block is not None
    assert block.reason is BlockReason.UNAVAILABLE
    assert await gate.refuse() == "spend_check_unavailable"


async def test_a_credit_flag_that_cant_be_read_fails_closed_too() -> None:
    class CreditFlagDown(InMemorySpendStore):
        async def credit_out(self, provider: str) -> bool:
            raise ConnectionError("redis went away between two reads")

    ticker = Ticker()
    gate, _, _ = gate_over(CreditFlagDown(ticker), ticker)
    block = await gate.app_block()
    assert block is not None
    assert block.reason is BlockReason.UNAVAILABLE


async def test_a_blocked_turns_reply_says_why_and_what_still_works() -> None:
    gate, _, _ = gate_over(kill_switch=True)
    block = await gate.app_block()
    assert block is not None
    assert "paused" in block.message
    assert "browse your memory" in block.reply
    assert "undo" in block.reply
    for reason in BlockReason:  # every reason has words
        assert reason.value
    assert all(
        r.value
        in {
            "kill_switch",
            "daily_cap",
            "monthly_cap",
            "provider_credit",
            "quota",
            "spend_check_unavailable",
        }
        for r in BlockReason
    )


async def test_a_warning_is_logged_once_per_period_at_the_warn_ratio() -> None:
    gate, _, _ = gate_over(warn_ratio=0.8)
    with capture_logs() as logs:
        await gate.spent("anthropic", Decimal("0.30"))
        await gate.spent("anthropic", Decimal("0.11"))  # 0.41 of 0.50 = 82%
        await gate.spent("anthropic", Decimal("0.02"))
    warnings = [e for e in logs if e["event"] == "spend.cap_warning"]
    assert [(w["cap"], w["period"]) for w in warnings] == [("daily", "2026-09-29")]
    assert warnings[0]["limit_usd"] == 0.5


async def test_a_turns_rate_limit_is_a_bucket_per_identity() -> None:
    gate, _, ticker = gate_over()
    waits = [await gate.rate_limited("ada", 3) for _ in range(4)]
    assert waits[:3] == [None, None, None]
    assert waits[3] is not None
    assert 19 < waits[3] <= 20  # 3 a minute: one every 20 s
    assert await gate.rate_limited("grace", 3) is None  # another identity has its own
    ticker.t += 21
    assert await gate.rate_limited("ada", 3) is None


class Ledger:
    """The reconcile job's source of truth: totals since a moment, per provider."""

    def __init__(self, rows: list[tuple[datetime, str, Decimal]]) -> None:
        self.rows = rows

    async def since(self, moment: datetime) -> Mapping[str, Decimal]:
        out: dict[str, Decimal] = {}
        for at, provider, cost in self.rows:
            if at >= moment:
                out[provider] = out.get(provider, Decimal(0)) + cost
        return out


async def test_reconcile_resets_the_counters_to_what_the_ledger_says() -> None:
    gate, store, _ = gate_over(credit_since=date(2026, 9, 1))
    await gate.spent("anthropic", Decimal("9.99"))  # drifted: a process died before its count
    ledger = Ledger(
        [
            (datetime(2026, 8, 30, 9, tzinfo=UTC), "anthropic", Decimal("1.00")),
            (datetime(2026, 9, 2, 9, tzinfo=UTC), "anthropic", Decimal("0.20")),
            (datetime(2026, 9, 29, 8, tzinfo=UTC), "openai", Decimal("0.05")),
            (datetime(2026, 9, 29, 9, tzinfo=UTC), "anthropic", Decimal("0.10")),
        ]
    )
    totals = await gate.reconcile(ledger)
    assert totals.day == Decimal("0.15")
    assert totals.month == Decimal("0.35")
    assert totals.providers == {"anthropic": Decimal("0.30"), "openai": Decimal("0.05")}
    assert await store.totals(NOW) == totals
    assert await gate.app_block() is None


@pytest.mark.parametrize("cost", ["0", "0.00000000"])
async def test_free_calls_leave_the_counters_alone(cost: str) -> None:
    gate, store, _ = gate_over()
    await gate.spent("openai", Decimal(cost))
    assert (await store.totals(NOW)).day == 0

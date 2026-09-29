"""R.10 / ADR-0032: the router's spend guard. A refusal leaves before any adapter is called; a
turn already admitted is not refused; a provider out of credit (by our count or its own error)
is skipped for its fallback; every priced call is counted."""

from decimal import Decimal

import pytest

from secondmind.config import Step
from secondmind.providers import (
    CallsRefusedError,
    ChatMessage,
    FakeOutcome,
    ModelRouter,
    ProviderErrorKind,
    ProviderUnavailableError,
)
from tests.unit.providers.helpers import fakes, router

HELLO = [ChatMessage.user("hello")]


class Guard:
    """A CallGuard a test controls."""

    def __init__(self) -> None:
        self.reason: str | None = None
        self.skip: set[str] = set()
        self.counted: list[tuple[str, Decimal]] = []
        self.out: list[str] = []

    async def refuse(self) -> str | None:
        return self.reason

    async def skip_provider(self, provider: str) -> bool:
        return provider in self.skip

    async def spent(self, provider: str, cost: Decimal) -> None:
        self.counted.append((provider, cost))

    async def out_of_credit(self, provider: str) -> None:
        self.out.append(provider)


def guarded(guard: Guard, *names: str, fallback: bool = False) -> tuple[ModelRouter, dict]:  # type: ignore[type-arg]
    adapters = fakes(*names)
    for adapter in adapters.values():
        adapter.script.add(FakeOutcome(text="fine"))
    return (
        router(adapters, f"{names[0]}:m", f"{names[1]}:m" if fallback else None).with_guard(guard),
        adapters,
    )


async def test_a_refusal_leaves_before_any_adapter_is_called() -> None:
    guard = Guard()
    guard.reason = "kill_switch"
    r, adapters = guarded(guard, "primary")
    with pytest.raises(CallsRefusedError) as caught:
        await r.chat(Step.ANSWER, system=None, messages=HELLO)
    assert caught.value.reason == "kill_switch"
    assert adapters["primary"].requests == []
    assert guard.counted == []

    with pytest.raises(CallsRefusedError):
        async for _ in r.stream(Step.ANSWER, system=None, messages=HELLO):
            pass
    assert adapters["primary"].requests == []


async def test_a_turn_already_admitted_is_not_refused_when_the_switch_flips() -> None:
    guard = Guard()
    r, adapters = guarded(guard, "primary")
    turn_router = r.admitted()
    guard.reason = "kill_switch"  # flipped after the turn was let in
    result = await turn_router.chat(Step.ANSWER, system=None, messages=HELLO)
    assert result.text == "fine"
    assert len(adapters["primary"].requests) == 1
    with pytest.raises(CallsRefusedError):  # anything that isn't part of that turn still is
        await r.chat(Step.ANSWER, system=None, messages=HELLO)


async def test_a_provider_out_of_credit_is_skipped_for_its_fallback() -> None:
    guard = Guard()
    guard.skip = {"primary"}
    r, adapters = guarded(guard, "primary", "backup", fallback=True)
    result = await r.chat(Step.ANSWER, system=None, messages=HELLO)
    assert result.call.provider == "backup"
    assert adapters["primary"].requests == []
    assert result.call.fallback is not None
    assert "skipped: out of credit" in result.call.fallback.reason


async def test_every_provider_out_of_credit_is_a_clean_failure() -> None:
    guard = Guard()
    guard.skip = {"primary", "backup"}
    r, adapters = guarded(guard, "primary", "backup", fallback=True)
    with pytest.raises(ProviderUnavailableError) as caught:
        await r.chat(Step.ANSWER, system=None, messages=HELLO)
    assert "skipped: out of credit" in caught.value.detail
    assert adapters["primary"].requests == adapters["backup"].requests == []


async def test_a_providers_credit_error_marks_it_and_falls_back_without_retrying() -> None:
    guard = Guard()
    adapters = fakes("primary", "backup")
    adapters["primary"].script.add(FakeOutcome(error=ProviderErrorKind.CREDIT))
    adapters["backup"].script.add(FakeOutcome(text="from backup"))
    r = router(adapters, "primary:m", "backup:m").with_guard(guard)
    result = await r.chat(Step.ANSWER, system=None, messages=HELLO)
    assert result.text == "from backup"
    assert len(adapters["primary"].requests) == 1  # no retries: more calls won't add credit
    assert guard.out == ["primary"]


async def test_every_priced_call_is_counted_with_its_provider() -> None:
    guard = Guard()
    r, _ = guarded(guard, "primary")
    result = await r.chat(Step.ANSWER, system=None, messages=HELLO)
    assert result.call.usage.cost_usd > 0
    assert guard.counted == [("primary", result.call.usage.cost_usd)]

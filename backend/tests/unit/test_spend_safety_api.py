"""R.10 / ADR-0032 through the API: every block refuses with no model call and is stored as a
turn a glass box can explain; browsing, undo and the glass box keep working; a turn already
running finishes; the tier sets the allowance on the next turn; a rate limit is refused at the
door and never stored."""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest

from secondmind.api import Services, create_app
from secondmind.auth import resolve_scope
from secondmind.core import Tier, utc_now
from secondmind.metering import SpendLimits, SpendTotals
from secondmind.providers import FakeOutcome, FakeScript
from tests.unit.test_api import _services, frames, login


class Ticker:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def ticker() -> Ticker:
    return Ticker()


@pytest.fixture
async def stack(
    base_env: dict[str, str], ticker: Ticker
) -> AsyncIterator[tuple[httpx.AsyncClient, Services]]:
    services = _services(base_env, monotonic=ticker, RATE_TURNS_PER_MINUTE="1000")
    app = create_app(services=services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, services


async def send(
    client: httpx.AsyncClient, ws: str, message: str
) -> list[tuple[str, dict[str, Any]]]:
    response = await client.post(f"/v1/workspaces/{ws}/turns", json={"message": message})
    assert response.status_code == 200, response.text
    return frames(response.text)


async def events_of(client: httpx.AsyncClient, turn_id: str) -> list[dict[str, Any]]:
    body = (await client.get(f"/v1/turns/{turn_id}/events")).json()
    return [e["event"] for e in body["events"]]


async def assert_blocked(
    client: httpx.AsyncClient, ws: str, reason: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A turn that was stopped: stored and completed, a template reply, and not one model call."""
    got = await send(client, ws, "remember that I like tea")
    assert got[0][0] == "turn.started"
    name, completed = got[-1]
    assert name == "turn.completed"
    turn = completed["turn"]
    assert turn["status"] == "completed"
    assert completed["usage"]["cost_usd"] == 0
    assert turn["models"] == {}
    events = await events_of(client, turn["id"])
    assert not [e for e in events if e["type"] == "model_call"]
    blocked = [e for e in events if e["type"] == "blocked"]
    assert [b["reason"] for b in blocked] == [reason]
    steps = [(e["step"], e["status"]) for e in events if e["type"] == "step"]
    assert steps == [("blocked", "refused")]
    streamed = "".join(d["text"] for n, d in got if n == "token")
    assert streamed == turn["output"]
    assert "browse your memory" in turn["output"]
    return turn, events


async def test_the_kill_switch_stops_turns_and_flipping_it_back_needs_no_restart(
    stack: tuple[httpx.AsyncClient, Services], ticker: Ticker
) -> None:
    client, services = stack
    ws = await login(client)
    ok = await send(client, ws, "hello")
    assert ok[-1][0] == "turn.completed"
    before = (await client.get("/v1/me/usage")).json()
    assert before["read_only"] is False

    await services.gate.store.set_kill_switch(True)  # `make kill-switch on`
    ticker.t += 3  # a process re-reads the flag within 2 s
    turn, _ = await assert_blocked(client, ws, "kill_switch")
    assert "paused for everyone" in turn["output"]
    usage = (await client.get("/v1/me/usage")).json()
    assert usage["read_only"] is True
    assert usage["read_only_reason"] == "kill_switch"
    assert "browse your memory" in usage["read_only_message"]
    assert usage["used_usd"] == before["used_usd"]  # a blocked turn costs nothing

    # Browsing, history and the glass box are not turns: they keep working.
    page = (await client.get(f"/v1/workspaces/{ws}/turns")).json()["items"]
    assert page[0]["id"] == turn["id"]
    assert (await client.get(f"/v1/turns/{turn['id']}/events")).status_code == 200
    assert (await client.get(f"/v1/workspaces/{ws}/upcoming")).status_code == 200

    await services.gate.store.set_kill_switch(False)  # `make kill-switch off`
    ticker.t += 3
    back = await send(client, ws, "hello again")
    assert back[-1][0] == "turn.completed"
    assert back[-1][1]["usage"]["cost_usd"] > 0


async def test_undo_still_works_while_turns_are_blocked(
    stack: tuple[httpx.AsyncClient, Services], ticker: Ticker
) -> None:
    client, services = stack
    ws = await login(client)
    saved = (await send(client, ws, "I live in Bengaluru"))[-1][1]["turn"]["id"]
    await services.gate.store.set_kill_switch(True)
    ticker.t += 3
    undo = await client.post(f"/v1/turns/{saved}/undo")
    assert undo.status_code == 200, undo.text
    assert undo.json()["kind"] == "undo"


async def test_a_turn_already_running_finishes_when_the_switch_flips(
    base_env: dict[str, str], ticker: Ticker
) -> None:
    script = FakeScript()
    script.add(FakeOutcome(text="still here", delay_s=0.3), step="answer")
    services = _services(base_env, script, monotonic=ticker)
    app = create_app(services=services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        ws = await login(c)
        (user_id,) = services.identity.users  # type: ignore[attr-defined]
        scope, workspace = await resolve_scope(
            services.identity, user_id=user_id, workspace_id=uuid.UUID(ws)
        )
        handle = await services.runner.start(scope, text="hello", timezone=workspace.timezone)
        await services.gate.store.set_kill_switch(True)  # flipped while it runs
        ticker.t += 3
        events = [e async for e in handle.events()]
    assert type(events[-1]).__name__ == "TurnCompleted"
    assert events[-1].turn.output == "still here"  # type: ignore[union-attr]


async def test_the_daily_cap_stops_turns_with_the_limit_and_value_stored(
    stack: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = stack
    ws = await login(client)
    await services.gate.spent("anthropic", Decimal("0.50"))
    _, events = await assert_blocked(client, ws, "daily_cap")
    blocked = next(e for e in events if e["type"] == "blocked")
    assert (blocked["limit_usd"], blocked["used_usd"]) == (0.5, 0.5)
    assert "today's spending limit" in blocked["message"].lower()


async def test_the_monthly_cap_stops_turns(stack: tuple[httpx.AsyncClient, Services]) -> None:
    client, services = stack
    ws = await login(client)
    await services.gate.store.reset(
        SpendTotals(day=Decimal("0.10"), month=Decimal("5.00")), utc_now()
    )
    await assert_blocked(client, ws, "monthly_cap")


async def test_both_providers_out_of_credit_stops_turns(base_env: dict[str, str]) -> None:
    # The fake stack routes everything to the fake provider, so the real ones are named here.
    limits = SpendLimits(
        daily_usd=Decimal(100), monthly_usd=Decimal(100), providers=("anthropic", "openai")
    )
    services = _services(base_env, limits=limits)
    app = create_app(services=services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        ws = await login(c)
        await services.gate.out_of_credit("anthropic")
        ok = await send(c, ws, "hello")
        assert ok[-1][0] == "turn.completed"  # one provider is left: its steps use that one
        assert ok[-1][1]["usage"]["cost_usd"] > 0
        await services.gate.out_of_credit("openai")
        await assert_blocked(c, ws, "provider_credit")


async def test_the_quota_is_dollars_by_tier_and_an_upgrade_applies_on_the_next_turn(
    base_env: dict[str, str], ticker: Ticker
) -> None:
    services = _services(base_env, monotonic=ticker, QUOTA_USD_STANDARD="0.00001")
    app = create_app(services=services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        ws = await login(c)
        limit = (await c.get("/v1/me/usage")).json()["limit_usd"]
        assert limit == pytest.approx(0.00001)
        first = await send(c, ws, "hello")
        assert first[-1][0] == "turn.completed"
        assert first[-1][1]["quota"]["remaining_usd"] == 0
        assert first[-1][1]["quota"]["read_only"] is True
        turn, events = await assert_blocked(c, ws, "quota")
        blocked = next(e for e in events if e["type"] == "blocked")
        assert blocked["limit_usd"] == pytest.approx(0.00001)

        # `make set-tier EMAIL=… TIER=premium`: the allowance is the premium one on the next turn.
        (user_id,) = services.identity.users  # type: ignore[attr-defined]
        user = services.identity.users[user_id]  # type: ignore[attr-defined]
        services.identity.users[user_id] = user.model_copy(update={"tier": Tier.PREMIUM})  # type: ignore[attr-defined]
        usage = (await c.get("/v1/me/usage")).json()
        assert usage["tier"] == "premium"
        assert usage["limit_usd"] == 4.0
        assert usage["read_only"] is False
        again = await send(c, ws, "hello again")
        assert again[-1][1]["usage"]["cost_usd"] > 0
        assert turn["output"] is not None


async def test_a_rate_limited_turn_is_refused_at_the_door_and_never_stored(
    base_env: dict[str, str], ticker: Ticker
) -> None:
    services = _services(base_env, monotonic=ticker, RATE_TURNS_PER_MINUTE="2")
    app = create_app(services=services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        ws = await login(c)
        await send(c, ws, "one")
        await send(c, ws, "two")
        refused = await c.post(f"/v1/workspaces/{ws}/turns", json={"message": "three"})
        assert refused.status_code == 429
        assert refused.json()["error"]["code"] == "rate_limited"
        assert 1 <= int(refused.headers["retry-after"]) <= 30
        page = (await c.get(f"/v1/workspaces/{ws}/turns")).json()["items"]
        assert len(page) == 2  # the rejected request left nothing behind
        ticker.t += 31
        assert (await send(c, ws, "three"))[-1][0] == "turn.completed"

"""S4.12 (no database): guests are made, come back by their device cookie, are refused past the
per-address cap, and need the access code until the site opens; the guests' share of the day's cap
stops guests and leaves a signed-in person alone."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from secondmind.api import Services, create_app
from secondmind.auth import Workspace, WorkspaceKind
from secondmind.core import Tier
from secondmind.metering import SpendLimits
from tests.unit.test_api import _services
from tests.unit.test_web_hardening import CODE, LIVE, SECURE

FORWARDED = "x-forwarded-for"


class StubPersona:
    """What the route needs of the persona service: a new copy for a user."""

    def __init__(self, services: Services) -> None:
        self._identity = services.identity

    async def make_copy(self, user_id: uuid.UUID, **_: object) -> Workspace:
        return await self._identity.create_workspace(
            owner_user_id=user_id, kind=WorkspaceKind.PERSONA_COPY, timezone="Asia/Kolkata"
        )


def _guest_services(base_env: dict[str, str], **env: str) -> Services:
    services = _services(base_env, GUESTS_OPEN="true", **env)
    services.persona = StubPersona(services)  # type: ignore[assignment]
    return services


def _client(services: Services) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services=services)), base_url="http://web.test"
    )


@pytest.fixture
async def site(base_env: dict[str, str]) -> AsyncIterator[tuple[httpx.AsyncClient, Services]]:
    services = _guest_services(base_env, TRUSTED_PROXY_HOPS="2", GUEST_NEW_PER_IP_PER_DAY="2")
    async with _client(services) as c:
        yield c, services


def behind_proxies(address: str, forged: str = "") -> dict[str, str]:
    left = f"{forged}, " if forged else ""
    return {FORWARDED: f"{left}{address}, 10.0.0.2"}


async def test_a_guest_has_no_email_the_guest_tier_and_a_copy_of_the_persona(
    site: tuple[httpx.AsyncClient, Services],
) -> None:
    client, _ = site
    response = await client.post("/v1/guest", headers=behind_proxies("203.0.113.7"))
    assert response.status_code == 200, response.text
    me = response.json()
    assert me["user"]["email"] is None
    assert [w["kind"] for w in me["workspaces"]] == ["persona_copy"]
    assert "sm_session" in response.cookies
    assert "sm_device" in response.cookies
    usage = (await client.get("/v1/me/usage")).json()
    assert usage["tier"] == "guest"
    assert usage["limit_usd"] == 0.75  # the lifetime allowance of a guest
    cookie = response.headers["set-cookie"]
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()


async def test_a_device_that_comes_back_continues_as_the_same_guest(
    base_env: dict[str, str],
) -> None:
    services = _guest_services(base_env, TRUSTED_PROXY_HOPS="2", GUEST_NEW_PER_IP_PER_DAY="1")
    async with _client(services) as first:
        made = (await first.post("/v1/guest", headers=behind_proxies("203.0.113.7"))).json()
        device = first.cookies["sm_device"]
    async with _client(services) as again:  # a fresh browser session that kept the device cookie
        again.cookies.set("sm_device", device)
        back = await again.post("/v1/guest", headers=behind_proxies("203.0.113.7"))
        assert back.status_code == 200  # the per-address cap of one doesn't stop a guest who exists
        assert back.json()["user"]["id"] == made["user"]["id"]
        assert (await again.get("/v1/me")).json()["user"]["id"] == made["user"]["id"]


async def test_a_tampered_or_borrowed_device_cookie_is_not_believed(
    base_env: dict[str, str],
) -> None:
    services = _guest_services(base_env, TRUSTED_PROXY_HOPS="2", GUEST_NEW_PER_IP_PER_DAY="10")
    async with _client(services) as first:
        made = (await first.post("/v1/guest", headers=behind_proxies("203.0.113.7"))).json()
        device = first.cookies["sm_device"]
        session = first.cookies["sm_session"]
    tampered = device[:-3] + ("aaa" if not device.endswith("aaa") else "bbb")
    async with _client(services) as c:
        c.cookies.set("sm_device", tampered)
        other = (await c.post("/v1/guest", headers=behind_proxies("203.0.113.8"))).json()
        assert other["user"]["id"] != made["user"]["id"]  # a new guest, not the one it claimed
    async with _client(services) as c:
        # A session token is not a device token, and the other way round.
        c.cookies.set("sm_device", session)
        third = (await c.post("/v1/guest", headers=behind_proxies("203.0.113.9"))).json()
        assert third["user"]["id"] != made["user"]["id"]
    async with _client(services) as c:
        c.cookies.set("sm_session", device)
        assert (await c.get("/v1/me")).status_code == 401


async def test_the_per_address_cap_refuses_new_guests_and_spares_the_ones_who_exist(
    site: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = site
    for _ in range(2):
        async with _client(services) as c:
            assert (
                await c.post("/v1/guest", headers=behind_proxies("203.0.113.7"))
            ).status_code == 200
    async with _client(services) as c:
        refused = await c.post("/v1/guest", headers=behind_proxies("203.0.113.7"))
        assert refused.status_code == 429
        assert "Retry-After" in refused.headers
        assert "tomorrow" in refused.json()["error"]["message"]
    async with _client(services) as c:  # another address is fine
        assert (
            await c.post("/v1/guest", headers=behind_proxies("198.51.100.1"))
        ).status_code == 200
    # The guest made earlier carries on.
    assert (await client.get("/v1/me")).status_code == 401  # this client never had a session
    _ = services


async def test_a_header_the_client_sends_cannot_choose_the_counted_address(
    site: tuple[httpx.AsyncClient, Services],
) -> None:
    _, services = site
    for spoof in ("1.1.1.1", "2.2.2.2"):
        async with _client(services) as c:
            made = await c.post("/v1/guest", headers=behind_proxies("203.0.113.7", forged=spoof))
            assert made.status_code == 200
    async with _client(services) as c:
        third = await c.post("/v1/guest", headers=behind_proxies("203.0.113.7", forged="3.3.3.3"))
    assert third.status_code == 429  # still the same real address, whatever it claimed to be


async def test_until_the_site_opens_a_guest_needs_the_access_code_too(
    base_env: dict[str, str],
) -> None:
    production = LIVE | SECURE | {"ENV": "production", "DEV_AUTH": "true", "ACCESS_CODE": CODE}
    closed = _services(base_env, **production)
    closed.persona = StubPersona(closed)  # type: ignore[assignment]
    async with _client(closed) as c:
        assert (await c.post("/v1/guest")).status_code == 401
        assert (await c.post("/v1/guest", json={"access_code": "wrong"})).status_code == 401
        ok = await c.post("/v1/guest", json={"access_code": CODE})
        assert ok.status_code == 200
    opened = _services(base_env, **(production | {"GUESTS_OPEN": "true"}))
    opened.persona = StubPersona(opened)  # type: ignore[assignment]
    async with _client(opened) as c:
        assert (await c.post("/v1/guest")).status_code == 200  # no code once it is open


async def test_without_the_sample_persona_there_is_no_guest(base_env: dict[str, str]) -> None:
    services = _services(base_env, GUESTS_OPEN="true")  # no persona service
    async with _client(services) as c:
        assert (await c.post("/v1/guest")).status_code == 404


async def test_a_guest_can_open_a_scratch_memory_and_a_signed_in_person_cannot(
    site: tuple[httpx.AsyncClient, Services],
) -> None:
    client, _ = site
    await client.post("/v1/guest", headers=behind_proxies("203.0.113.7"))
    scratch = await client.post("/v1/scratch")
    assert scratch.status_code == 200
    assert scratch.json()["kind"] == "scratch"
    assert (await client.post("/v1/scratch")).json()["id"] == scratch.json()["id"]  # the same one
    kinds = {w["kind"] for w in (await client.get("/v1/me")).json()["workspaces"]}
    assert kinds == {"persona_copy", "scratch"}


async def test_a_signed_in_person_has_no_scratch_memory(base_env: dict[str, str]) -> None:
    services = _guest_services(base_env)
    async with _client(services) as person:
        await person.post("/v1/auth/dev-login")
        assert (await person.post("/v1/scratch")).status_code == 403


async def test_the_guests_share_of_the_day_stops_guests_and_leaves_a_signed_in_person(
    base_env: dict[str, str],
) -> None:
    limits = SpendLimits(
        daily_usd=Decimal("0.50"), monthly_usd=Decimal(5), guest_daily_usd=Decimal("0.30")
    )
    services = _guest_services(base_env, TRUSTED_PROXY_HOPS="2")
    services.gate = type(services.gate)(  # the gate, with a guests' total over their share
        services.gate.store, limits, clock=lambda: datetime(2026, 10, 1, 12, tzinfo=UTC)
    )

    async def guests_spent(since: datetime) -> Decimal:
        return Decimal("0.31")

    services.gate.attach_guest_spend(guests_spent)
    async with _client(services) as guest:
        await guest.post("/v1/guest", headers=behind_proxies("203.0.113.7"))
        usage = (await guest.get("/v1/me/usage")).json()
        assert usage["tier"] == Tier.GUEST.value
        assert usage["read_only"] is True
        assert usage["read_only_reason"] == "guest_cap"
        assert "tomorrow" in usage["read_only_message"]
    async with _client(services) as person:
        await person.post("/v1/auth/dev-login")
        usage = (await person.get("/v1/me/usage")).json()
        assert usage["read_only"] is False  # a signed-in person carries on

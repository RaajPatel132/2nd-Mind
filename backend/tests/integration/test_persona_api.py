"""S4.11 through /v1: a signed-in person opens their copy of the sample persona, gets the same one
again, resets it for a fresh one, and none of it calls a model."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from secondmind.api import Services, build_services, create_app
from secondmind.auth.adapters import SqlIdentityStore
from secondmind.config import load_app_config
from secondmind.memory.adapters import Database
from tests.conftest import BASE_ENV
from tests.integration.conftest import PgUrls
from tests.integration.persona_seed import PersonaWorld, load_template

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
async def world(app_db: Database, identity: SqlIdentityStore) -> PersonaWorld:
    return await load_template(app_db, identity)


@asynccontextmanager
async def _client(
    pg_urls: PgUrls, redis_url: str, email: str, extra: dict[str, str] | None = None
) -> AsyncIterator[tuple[httpx.AsyncClient, Services]]:
    env = (
        BASE_ENV
        | {
            "DATABASE_URL": pg_urls.app,
            "REDIS_URL": redis_url,
            "MODEL_PROVIDER_MODE": "fake",
            "FAKE_PROVIDER_TOKEN_DELAY_MS": "0",
            "DEV_AUTH": "true",
            "DEV_USER_EMAIL": email,
        }
        | (extra or {})
    )
    services = await build_services(load_app_config(env))
    app = create_app(services=services)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            yield c, services
    finally:
        await services.aclose()


def _fake_calls(services: Services) -> int:
    provider = services.runner._router._adapters["fake"]  # type: ignore[attr-defined]
    return len(provider.requests) + len(provider.embed_calls)


async def test_a_visitor_opens_resets_and_reopens_their_copy_without_a_model_call(
    world: PersonaWorld, pg_urls: PgUrls, redis_url: str
) -> None:
    async with _client(pg_urls, redis_url, "persona-api@example.test") as (client, services):
        me = (await client.post("/v1/auth/dev-login")).json()
        private = me["workspaces"][0]["id"]
        before = _fake_calls(services)

        opened = await client.post("/v1/persona")
        assert opened.status_code == 200, opened.text
        copy = opened.json()
        assert copy["kind"] == "persona_copy"
        assert copy["seed_version"] == 1
        assert copy["moved_days"] >= 0
        again = (await client.post("/v1/persona")).json()
        assert again["id"] == copy["id"]  # the same one, not another

        names = {w["kind"] for w in (await client.get("/v1/me")).json()["workspaces"]}
        assert names == {"private", "persona_copy"}  # the template is never listed

        turns = (await client.get(f"/v1/workspaces/{copy['id']}/turns?limit=30")).json()["items"]
        assert len([t for t in turns if t["kind"] == "user"]) == 10  # Aditi's past chats
        assert _fake_calls(services) == before  # opening it called no model

        fresh = (await client.post("/v1/persona/reset")).json()
        assert fresh["id"] != copy["id"]
        assert fresh["kind"] == "persona_copy"
        gone = await client.get(f"/v1/workspaces/{copy['id']}/turns")
        assert gone.status_code == 404
        assert private != fresh["id"]
        assert _fake_calls(services) == before


async def test_what_was_upcoming_is_still_upcoming_in_a_copy_made_now(
    world: PersonaWorld, pg_urls: PgUrls, redis_url: str
) -> None:
    async with _client(pg_urls, redis_url, "persona-upcoming@example.test") as (client, _):
        await client.post("/v1/auth/dev-login")
        copy = (await client.post("/v1/persona")).json()
        upcoming = (await client.get(f"/v1/workspaces/{copy['id']}/upcoming?days=30")).json()
        titles = [e["title"] for day in upcoming["days"] for e in day["entries"]]
        assert any("Send design review notes to Priya" in t for t in titles), titles


async def test_the_persona_needs_a_signed_in_person(
    world: PersonaWorld, pg_urls: PgUrls, redis_url: str
) -> None:
    async with _client(pg_urls, redis_url, "persona-anon@example.test") as (client, _):
        assert (await client.post("/v1/persona")).status_code == 401
        assert (await client.post("/v1/persona/reset")).status_code == 401

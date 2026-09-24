"""The real composition root against real Postgres and Redis: readiness, and one streamed
turn persisted through RLS-scoped stores with its usage-ledger row."""

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text

from secondmind.api import build_services, create_app
from secondmind.config import load_app_config
from secondmind.memory.adapters import Database
from tests.conftest import BASE_ENV
from tests.integration.conftest import PgUrls

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(pg_urls: PgUrls, redis_url: str) -> AsyncIterator[httpx.AsyncClient]:
    env = BASE_ENV | {
        "DATABASE_URL": pg_urls.app,
        "REDIS_URL": redis_url,
        "MODEL_PROVIDER_MODE": "fake",
        "FAKE_PROVIDER_TOKEN_DELAY_MS": "0",
        "DEV_AUTH": "true",
        "DEV_USER_EMAIL": "stack-test@example.test",
    }
    services = await build_services(load_app_config(env))
    app = create_app(services=services)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            yield c
    finally:
        await services.aclose()


async def test_readiness_checks_real_dependencies(client: httpx.AsyncClient) -> None:
    body = (await client.get("/readyz")).json()
    assert body["status"] == "ready", body
    assert "under RLS" in body["checks"]["database"]["detail"]
    assert body["checks"]["redis"]["ok"]


async def test_turn_is_persisted_with_events_and_ledger(
    client: httpx.AsyncClient, owner_db: Database
) -> None:
    ws = (await client.post("/v1/auth/dev-login")).json()["workspaces"][0]["id"]
    response = await client.post(f"/v1/workspaces/{ws}/turns", json={"message": "hello db"})
    blocks = [b for b in response.text.strip().split("\n\n") if b.startswith("event:")]
    last = dict(line.split(": ", 1) for line in blocks[-1].splitlines())
    assert last["event"] == "turn.completed"
    turn_id = json.loads(last["data"])["turn_id"]

    events = (await client.get(f"/v1/turns/{turn_id}/events")).json()["events"]
    assert [e["event"]["type"] for e in events] == ["intent", "model_call"]

    async with owner_db.identity() as session:
        ledger = (
            await session.execute(
                text(
                    "SELECT l.provider, l.input_tokens + l.output_tokens, w.owner_user_id = "
                    "l.owner_user_id FROM usage_ledger l JOIN workspaces w "
                    "ON w.id = l.workspace_id WHERE l.turn_id = :t"
                ),
                {"t": turn_id},
            )
        ).one()
    assert ledger[0] == "fake"
    assert ledger[1] > 0
    assert ledger[2] is True

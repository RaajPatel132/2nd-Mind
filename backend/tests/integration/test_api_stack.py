"""The real composition root against real Postgres and Redis: readiness, one streamed turn
persisted through RLS-scoped stores with its usage-ledger rows, and save → supersede → undo
through /v1 (S2.12) on the offline fake brain."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from secondmind.api import build_services, create_app
from secondmind.config import load_app_config
from secondmind.memory.adapters import Database
from tests.conftest import BASE_ENV
from tests.integration.conftest import PgUrls

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _client(pg_urls: PgUrls, redis_url: str, email: str) -> AsyncIterator[httpx.AsyncClient]:
    env = BASE_ENV | {
        "DATABASE_URL": pg_urls.app,
        "REDIS_URL": redis_url,
        "MODEL_PROVIDER_MODE": "fake",
        "FAKE_PROVIDER_TOKEN_DELAY_MS": "0",
        "DEV_AUTH": "true",
        "DEV_USER_EMAIL": email,
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


@pytest.fixture
async def client(pg_urls: PgUrls, redis_url: str) -> AsyncIterator[httpx.AsyncClient]:
    async with _client(pg_urls, redis_url, "stack-test@example.test") as c:
        yield c


async def _turn(client: httpx.AsyncClient, ws: str, message: str) -> str:
    response = await client.post(f"/v1/workspaces/{ws}/turns", json={"message": message})
    blocks = [b for b in response.text.strip().split("\n\n") if b.startswith("event:")]
    last = dict(line.split(": ", 1) for line in blocks[-1].splitlines())
    assert last["event"] == "turn.completed", response.text
    return str(json.loads(last["data"])["turn_id"])


async def _diff(client: httpx.AsyncClient, turn_id: str) -> dict[str, Any]:
    events = (await client.get(f"/v1/turns/{turn_id}/events")).json()["events"]
    diffs = [e["event"] for e in events if e["event"]["type"] == "memory_diff"]
    assert len(diffs) == 1, [e["event"]["type"] for e in events]
    return dict(diffs[0])


async def test_readiness_checks_real_dependencies(client: httpx.AsyncClient) -> None:
    body = (await client.get("/readyz")).json()
    assert body["status"] == "ready", body
    assert "under RLS" in body["checks"]["database"]["detail"]
    assert body["checks"]["redis"]["ok"]


async def test_turn_is_persisted_with_events_and_ledger(
    client: httpx.AsyncClient, owner_db: Database
) -> None:
    ws = (await client.post("/v1/auth/dev-login")).json()["workspaces"][0]["id"]
    turn_id = await _turn(client, ws, "hello db")

    events = (await client.get(f"/v1/turns/{turn_id}/events")).json()["events"]
    assert [e["event"]["type"] for e in events] == [
        "model_call",
        "intent",
        "step",
        "model_call",
        "step",
    ]

    async with owner_db.identity() as session:
        ledger = (
            await session.execute(
                text(
                    "SELECT l.step, l.provider, l.input_tokens + l.output_tokens, "
                    "w.owner_user_id = l.owner_user_id FROM usage_ledger l JOIN workspaces w "
                    "ON w.id = l.workspace_id WHERE l.turn_id = :t ORDER BY l.step"
                ),
                {"t": turn_id},
            )
        ).all()
    assert [row[0] for row in ledger] == ["answer", "intent"]
    for _, provider, tokens, charged_to_owner in ledger:
        assert provider == "fake"
        assert tokens > 0
        assert charged_to_owner is True


async def test_save_then_supersede_then_undo_through_the_api(
    pg_urls: PgUrls, redis_url: str
) -> None:
    async with _client(pg_urls, redis_url, "supersede-test@example.test") as client:
        ws = (await client.post("/v1/auth/dev-login")).json()["workspaces"][0]["id"]

        saved = await _turn(client, ws, "I live in Bengaluru")
        added = [e for e in (await _diff(client, saved))["entries"] if e["op"] == "added"]
        bengaluru = next(e["item_id"] for e in added if e["item_id"] and "Bengaluru" in e["title"])

        moved = await _turn(client, ws, "I moved to Pune")
        entries = (await _diff(client, moved))["entries"]
        assert "superseded" in [e["op"] for e in entries]
        old = (await client.get(f"/v1/items/{bengaluru}")).json()["item"]
        assert old["state"] == "superseded"
        assert old["valid_to"] is not None

        undo = await client.post(f"/v1/turns/{moved}/undo")
        assert undo.status_code == 200, undo.text
        body = undo.json()
        assert (body["kind"], body["parent_turn_id"]) == ("undo", moved)
        assert (await _diff(client, body["id"]))["undo_of"] == moved
        old = (await client.get(f"/v1/items/{bengaluru}")).json()["item"]
        assert (old["state"], old["valid_to"]) == ("current", None)


def _completed(response: httpx.Response) -> dict[str, Any]:
    blocks = [b for b in response.text.strip().split("\n\n") if b.startswith("event:")]
    last = dict(line.split(": ", 1) for line in blocks[-1].splitlines())
    assert last["event"] == "turn.completed", response.text
    return dict(json.loads(last["data"]))


async def test_usage_sums_the_users_ledger_and_no_one_else_sees_it(
    pg_urls: PgUrls, redis_url: str, owner_db: Database
) -> None:
    """UI.7: used = the user's ledger across their workspaces; the limit comes from
    QUOTA_TOKENS_STANDARD; turn.completed carries the same block; another user sees only
    their own."""
    async with _client(pg_urls, redis_url, "quota-a@example.test") as c:
        me = (await c.post("/v1/auth/dev-login")).json()
        ws, user_id = me["workspaces"][0]["id"], me["user"]["id"]
        before = (await c.get("/v1/me/usage")).json()
        assert before == {
            "tier": "standard",
            "limit_tokens": 1_000_000,
            "used_tokens": 0,
            "remaining_tokens": 1_000_000,
        }
        first = _completed(await c.post(f"/v1/workspaces/{ws}/turns", json={"message": "hi"}))
        second = _completed(await c.post(f"/v1/workspaces/{ws}/turns", json={"message": "yo"}))
        after = (await c.get("/v1/me/usage")).json()

        async with owner_db.identity() as session:
            ledger = (
                await session.execute(
                    text(
                        "SELECT COALESCE(SUM(input_tokens + cached_input_tokens + output_tokens),"
                        " 0) FROM usage_ledger WHERE owner_user_id = :u"
                    ),
                    {"u": user_id},
                )
            ).scalar_one()
        assert ledger > 0
        assert after["used_tokens"] == ledger
        assert after["remaining_tokens"] == 1_000_000 - ledger
        # The frame's block is the quota as it stood right after that turn.
        assert second["quota"] == after
        assert first["quota"]["used_tokens"] < second["quota"]["used_tokens"]

        other = (await c.post("/v1/auth/dev-login", json={"email": "quota-b@example.test"})).json()
        assert other["user"]["id"] != user_id
        theirs = (await c.get("/v1/me/usage")).json()
        assert theirs["used_tokens"] == 0
        assert theirs["remaining_tokens"] == 1_000_000

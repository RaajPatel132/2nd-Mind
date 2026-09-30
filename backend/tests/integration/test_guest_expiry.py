"""S4.12 on Postgres: a guest's memory is emptied after the time to live, the costs stay, two guests
never see each other, and a guest message holds one link."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

from secondmind.auth.adapters import GUEST_EMPTIED, GUEST_KEPT, SqlIdentityStore
from secondmind.memory.adapters import Database
from tests.integration.persona_seed import PersonaWorld, load_template
from tests.integration.test_persona_api import _client

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
async def world(app_db: Database, identity: SqlIdentityStore) -> PersonaWorld:
    return await load_template(app_db, identity)


async def _say(client: httpx.AsyncClient, workspace: str, message: str) -> str:
    response = await client.post(f"/v1/workspaces/{workspace}/turns", json={"message": message})
    blocks = [b for b in response.text.strip().split("\n\n") if b.startswith("event:")]
    last = dict(line.split(": ", 1) for line in blocks[-1].splitlines())
    assert last["event"] == "turn.completed", response.text
    return str(json.loads(last["data"])["turn_id"])


async def _guest(client: httpx.AsyncClient) -> dict:  # type: ignore[type-arg]
    response = await client.post("/v1/guest")
    assert response.status_code == 200, response.text
    return dict(response.json())


ENV = {"GUESTS_OPEN": "true", "GUEST_NEW_PER_IP_PER_DAY": "1000", "TRUSTED_PROXY_HOPS": "1"}


async def test_every_table_with_content_is_emptied_or_kept_on_purpose(owner_db: Database) -> None:
    async with owner_db.engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT DISTINCT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'workspace_id'"
            )
        )
        tables = {r[0] for r in rows}
    assert tables == GUEST_EMPTIED | GUEST_KEPT, (
        f"a workspace-owned table guest expiry doesn't know: "
        f"{sorted(tables - GUEST_EMPTIED - GUEST_KEPT)} (S4.12)"
    )


async def test_a_guests_memory_is_emptied_after_the_ttl_and_the_costs_stay(
    world: PersonaWorld,
    pg_urls,
    redis_url,
    owner_db: Database,
    identity: SqlIdentityStore,  # type: ignore[no-untyped-def]
) -> None:
    async with _client(pg_urls, redis_url, "expiry@example.test", ENV) as (client, _):
        me = await _guest(client)
        workspace = me["workspaces"][0]["id"]
        user_id = me["user"]["id"]
        await _say(client, workspace, "hello there")
        device = client.cookies["sm_device"]

        async def live_rows() -> dict[str, int]:
            out: dict[str, int] = {}
            async with owner_db.engine.connect() as conn:
                for table in sorted(GUEST_EMPTIED | GUEST_KEPT):
                    query = text(f"SELECT count(*) FROM {table} WHERE workspace_id = :w")  # noqa: S608
                    out[table] = int((await conn.execute(query, {"w": workspace})).scalar_one())
            return out

        before = await live_rows()
        assert before["memory_items"] > 100  # the copy of Aditi's memory
        assert before["usage_ledger"] > 0
        cost_before = await _guest_cost(owner_db, user_id)

        # Not yet: a guest made a moment ago isn't older than a week.
        assert await identity.expire_guests(datetime.now(UTC) - timedelta(days=7)) >= 0
        assert (await live_rows())["memory_items"] == before["memory_items"]

        emptied = await identity.expire_guests(datetime.now(UTC) + timedelta(seconds=5))
        assert emptied >= 1
        after = await live_rows()
        for table in GUEST_EMPTIED:
            expected = 1 if table == "entities" else 0  # the workspace's own "me" stays, cleared
            assert after[table] == expected, (table, after[table])
        assert after["usage_ledger"] == before["usage_ledger"]  # the ledger is kept, whole
        assert await _guest_cost(owner_db, user_id) == cost_before  # so the totals stay right
        async with owner_db.engine.connect() as conn:
            gone = (
                await conn.execute(
                    text("SELECT expired_at IS NOT NULL FROM workspaces WHERE id = :w"),
                    {"w": workspace},
                )
            ).scalar_one()
            orphans = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM usage_ledger "
                        "WHERE workspace_id = :w AND turn_id IS NOT NULL"
                    ),
                    {"w": workspace},
                )
            ).scalar_one()
        assert gone is True
        assert orphans == 0  # their turns are gone; the rows say so with no turn

        # An expired guest has nothing to open, and their device is a new guest from here.
        assert (await client.get("/v1/me")).json()["workspaces"] == []
    async with _client(pg_urls, redis_url, "expiry2@example.test", ENV) as (client, _):
        client.cookies.set("sm_device", device)
        again = await _guest(client)
        assert again["user"]["id"] != user_id
        assert [w["kind"] for w in again["workspaces"]] == ["persona_copy"]


async def _guest_cost(owner_db: Database, user_id: str) -> float:
    async with owner_db.engine.connect() as conn:
        value = (
            await conn.execute(
                text(
                    "SELECT coalesce(sum(cost_usd), 0) FROM usage_ledger WHERE owner_user_id = :u"
                ),
                {"u": user_id},
            )
        ).scalar_one()
    return float(value)


async def test_two_guests_never_see_each_other(
    world: PersonaWorld,
    pg_urls,
    redis_url,  # type: ignore[no-untyped-def]
) -> None:
    async with (
        _client(pg_urls, redis_url, "iso-a@example.test", ENV) as (a, _),
        _client(pg_urls, redis_url, "iso-b@example.test", ENV) as (b, _),
    ):
        first, second = await _guest(a), await _guest(b)
        ws_a, ws_b = first["workspaces"][0]["id"], second["workspaces"][0]["id"]
        assert ws_a != ws_b
        assert (await a.get(f"/v1/workspaces/{ws_b}/turns")).status_code == 404
        assert (await b.get(f"/v1/workspaces/{ws_a}/turns")).status_code == 404
        turn = await _say(a, ws_a, "a private thought")
        assert (await b.get(f"/v1/turns/{turn}/events")).status_code == 404
        mine = (await b.get(f"/v1/workspaces/{ws_b}/turns?limit=50")).json()["items"]
        assert all("private thought" not in t["input"] for t in mine)


async def test_a_guest_message_holds_one_link(
    world: PersonaWorld,
    pg_urls,
    redis_url,  # type: ignore[no-untyped-def]
) -> None:
    async with _client(pg_urls, redis_url, "links@example.test", ENV) as (client, _):
        me = await _guest(client)
        workspace = me["workspaces"][0]["id"]
        turn = await _say(
            client, workspace, "read https://one.example/a and https://two.example/b please"
        )
        events = (await client.get(f"/v1/turns/{turn}/events")).json()["events"]
        fetches = [e["event"] for e in events if e["event"]["type"] == "fetch"]
        assert sorted(f["status"] for f in fetches) == ["pending", "refused"]
        refused = next(f for f in fetches if f["status"] == "refused")
        assert refused["rule"] == "too_many_links"  # a signed-in person may send three

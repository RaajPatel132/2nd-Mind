"""S1.11: the /v1 API over in-memory stores and the fake provider (no database)."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from secondmind.agent import TurnRunner
from secondmind.api import CheckResult, Services, create_app
from secondmind.api.openapi import render
from secondmind.auth import SessionSigner
from secondmind.config import DEFAULT_RESOURCES_DIR, load_app_config
from secondmind.ingestion import load_replay, offline_responders
from secondmind.memory import Memory
from secondmind.memory.adapters import InMemoryMemory
from secondmind.observability import NullTracer
from secondmind.providers import FakeOutcome, FakeScript, ProviderErrorKind
from secondmind.providers.adapters import build_router
from tests.fakes import InMemoryIdentity, InMemoryTurns


def _services(base_env: dict[str, str], script: FakeScript | None = None, **env: str) -> Services:
    config = load_app_config(
        base_env
        | {"MODEL_PROVIDER_MODE": "fake", "FAKE_PROVIDER_TOKEN_DELAY_MS": "0", "DEV_AUTH": "true"}
        | env
    )
    turns = InMemoryTurns()
    script = script or FakeScript()
    script.responders = script.responders or offline_responders()

    async def ok() -> CheckResult:
        return CheckResult(ok=True, detail="ok")

    return Services(
        config=config,
        identity=InMemoryIdentity(),
        runner=TurnRunner(
            router=build_router(config, fake_script=script),
            prompts=config.prompts,
            stores=turns.store,
            tracer=NullTracer(),
            config_hash=config.config_hash,
            max_message_chars=config.settings.max_message_chars,
            memory=Memory(InMemoryMemory().store),
        ),
        tracer=NullTracer(),
        signer=SessionSigner(config.settings.session_secret.get_secret_value()),
        checks={"database": ok, "redis": ok},
    )


@pytest.fixture
async def client(base_env: dict[str, str]) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(services=_services(base_env))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def login(c: httpx.AsyncClient) -> str:
    response = await c.post("/v1/auth/dev-login")
    assert response.status_code == 200
    return str(response.json()["workspaces"][0]["id"])


def frames(body: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        if lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


async def test_health_and_readiness(client: httpx.AsyncClient) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    ready = await client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


async def test_readiness_fails_with_the_failing_check(base_env: dict[str, str]) -> None:
    services = _services(base_env)

    async def down() -> CheckResult:
        raise ConnectionError("db down")

    services.checks = {"database": down}
    app = create_app(services=services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        response = await c.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == {
        "ok": False,
        "detail": "ConnectionError: db down",
    }


async def test_meta_exposes_config_hash_and_routing(client: httpx.AsyncClient) -> None:
    body = (await client.get("/v1/meta")).json()
    assert len(body["config_hash"]) == 64
    assert body["config_hash_short"] == body["config_hash"][:12]
    assert body["provider_mode"] == "fake"
    answer = next(r for r in body["routes"] if r["step"] == "answer")
    assert answer == {
        "step": "answer",
        "provider": "fake",
        "model": "fake-chat",
        "fallback": None,
        "prompt": "answer@2",
        "timeout_s": 30.0,
    }


async def test_endpoints_require_a_session(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/me", headers={"x-request-id": "req-abc-12345"})
    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "unauthenticated",
            "message": "Sign in first.",
            "request_id": "req-abc-12345",
        }
    }
    assert response.headers["x-request-id"] == "req-abc-12345"


async def test_dev_login_is_404_when_dev_auth_is_off(base_env: dict[str, str]) -> None:
    app = create_app(services=_services(base_env, DEV_AUTH="false"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/v1/auth/dev-login")).status_code == 404


async def test_dev_login_can_name_another_dev_user_with_their_own_workspace(
    client: httpx.AsyncClient,
) -> None:
    default = await login(client)
    other = await client.post("/v1/auth/dev-login", json={"email": "e2e-1@example.test"})
    assert other.status_code == 200
    body = other.json()
    assert body["user"]["email"] == "e2e-1@example.test"
    assert body["workspaces"][0]["id"] != default
    me = (await client.get("/v1/me")).json()
    assert me["user"]["email"] == "e2e-1@example.test"
    bad = await client.post("/v1/auth/dev-login", json={"email": "not an email"})
    assert bad.status_code == 422


async def test_turn_streams_then_history_turn_and_events_are_readable(
    client: httpx.AsyncClient,
) -> None:
    ws = await login(client)
    response = await client.post(f"/v1/workspaces/{ws}/turns", json={"message": "hello"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    got = frames(response.text)
    names = [name for name, _ in got]
    assert names[0] == "turn.started"
    assert names[-1] == "turn.completed"
    assert names.count("token") >= 3
    turn_id = got[0][1]["turn_id"]
    completed = got[-1][1]
    assert completed["turn_id"] == turn_id
    assert completed["usage"]["output_tokens"] > 0
    streamed = "".join(d["text"] for n, d in got if n == "token")
    assert streamed == completed["turn"]["output"]
    assert 'You said: "hello"' in streamed

    page = (await client.get(f"/v1/workspaces/{ws}/turns")).json()
    assert [t["id"] for t in page["items"]] == [turn_id]
    turn = (await client.get(f"/v1/turns/{turn_id}")).json()
    assert turn["status"] == "completed"
    assert turn["models"]["answer"]["provider"] == "fake"
    assert turn["trace"] == {"status": "disabled", "url": None}

    events = (await client.get(f"/v1/turns/{turn_id}/events")).json()["events"]
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert [e["event"]["type"] for e in events] == ["model_call", "intent", "model_call"]
    assert events[2]["event"]["usage"]["cost_usd"] > 0


async def test_history_pages_with_before_cursor(client: httpx.AsyncClient) -> None:
    ws = await login(client)
    for i in range(3):
        await client.post(f"/v1/workspaces/{ws}/turns", json={"message": f"m{i}"})
    first = (await client.get(f"/v1/workspaces/{ws}/turns", params={"limit": 2})).json()
    assert [t["input"] for t in first["items"]] == ["m2", "m1"]
    second = (
        await client.get(
            f"/v1/workspaces/{ws}/turns", params={"limit": 2, "before": first["next_before"]}
        )
    ).json()
    assert [t["input"] for t in second["items"]] == ["m0"]
    assert second["next_before"] is None


async def test_provider_outage_streams_turn_failed(base_env: dict[str, str]) -> None:
    script = FakeScript()
    script.add(FakeOutcome(error=ProviderErrorKind.SERVER))
    app = create_app(services=_services(base_env, script))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        ws = await login(c)
        got = frames((await c.post(f"/v1/workspaces/{ws}/turns", json={"message": "hi"})).text)
    name, data = got[-1]
    assert name == "turn.failed"
    assert data["error"] == {
        "code": "provider_unavailable",
        "message": "The model provider is unavailable right now. Please try again in a moment.",
    }
    assert data["turn"]["output"] is None


async def test_other_users_workspace_is_not_found(
    client: httpx.AsyncClient, base_env: dict[str, str]
) -> None:
    await login(client)
    other = "01000000-0000-7000-8000-000000000000"
    response = await client.post(f"/v1/workspaces/{other}/turns", json={"message": "hi"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_invalid_body_uses_the_error_shape(client: httpx.AsyncClient) -> None:
    ws = await login(client)
    response = await client.post(f"/v1/workspaces/{ws}/turns", json={"message": ""})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_openapi_snapshot_is_up_to_date() -> None:
    committed = (DEFAULT_RESOURCES_DIR / "openapi.json").read_text()
    assert committed == render(), "run `make gen-client` and commit backend/openapi.json"


def test_openapi_documents_the_sse_frames() -> None:
    schema = json.loads(render())
    content = schema["paths"]["/v1/workspaces/{workspace_id}/turns"]["post"]["responses"]["200"][
        "content"
    ]
    assert list(content) == ["text/event-stream"]
    frame_events = {
        f["properties"]["event"]["const"]
        for f in schema["components"]["schemas"]["TurnStreamFrame"]["oneOf"]
    }
    assert frame_events == {"turn.started", "token", "turn.completed", "turn.failed"}


async def _turn_id(c: httpx.AsyncClient, ws: str, message: str) -> str:
    response = await c.post(f"/v1/workspaces/{ws}/turns", json={"message": message})
    name, data = frames(response.text)[-1]
    assert name == "turn.completed", data
    return str(data["turn_id"])


async def test_held_writes_can_be_listed_confirmed_once_and_are_private(
    base_env: dict[str, str],
) -> None:
    """S2.3 / S2.11 through /v1: a held core write is listed, confirmed as its own turn, and
    neither it nor the memory is visible to another user."""
    replay = load_replay(DEFAULT_RESOURCES_DIR / "evals" / "cases" / "ingest")
    app = create_app(
        services=_services(base_env, FakeScript(responders=offline_responders(replay)))
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        ws = await login(c)
        saved = await _turn_id(c, ws, "I've been seeing a therapist for anxiety since March")
        [held] = (await c.get(f"/v1/workspaces/{ws}/held-writes?status=pending")).json()["items"]
        assert (held["rule_id"], held["turn_id"], held["layer"]) == ("P-SENS-1", saved, "core")
        events = (await c.get(f"/v1/turns/{saved}/events")).json()["events"]
        diff = next(e["event"] for e in events if e["event"]["type"] == "memory_diff")
        item_id = next(e["item_id"] for e in diff["entries"] if e["op"] == "held")

        confirmed = await c.post(f"/v1/held-writes/{held['id']}/confirm")
        assert confirmed.status_code == 200, confirmed.text
        assert (confirmed.json()["kind"], confirmed.json()["parent_turn_id"]) == ("confirm", saved)
        again = await c.post(f"/v1/held-writes/{held['id']}/reject")
        assert again.status_code == 422
        item = (await c.get(f"/v1/items/{item_id}")).json()
        assert item["item"]["in_core"] is True

        await c.post("/v1/auth/dev-login", json={"email": "someone-else@example.test"})
        assert (await c.get(f"/v1/items/{item_id}")).status_code == 404
        assert (await c.post(f"/v1/held-writes/{held['id']}/confirm")).status_code == 404
        assert (await c.post(f"/v1/turns/{saved}/undo")).status_code == 404

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
        "prompt": "answer@1",
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
    assert [e["seq"] for e in events] == [1, 2]
    assert [e["event"]["type"] for e in events] == ["intent", "model_call"]
    assert events[1]["event"]["usage"]["cost_usd"] > 0


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

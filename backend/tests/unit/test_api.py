"""S1.11: the /v1 API over in-memory stores and the fake provider (no database)."""

import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from secondmind.agent import TurnRunner
from secondmind.api import CheckResult, Services, create_app
from secondmind.api.openapi import render
from secondmind.api.services import quota_limits
from secondmind.auth import SessionSigner
from secondmind.config import DEFAULT_RESOURCES_DIR, load_app_config
from secondmind.core import Tier
from secondmind.ingestion import load_replay, offline_responders
from secondmind.memory import Memory
from secondmind.memory.adapters import InMemoryMemory
from secondmind.metering import Quotas, SpendGate, SpendLimits
from secondmind.metering.adapters import spend_limits
from secondmind.observability import NullTracer
from secondmind.providers import FakeOutcome, FakeScript, ProviderErrorKind
from secondmind.providers.adapters import build_router
from tests.fakes import InMemoryIdentity, InMemorySpendStore, InMemoryTurns


def _services(
    base_env: dict[str, str],
    script: FakeScript | None = None,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    limits: SpendLimits | None = None,
    **env: str,
) -> Services:
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

    gate = SpendGate(
        InMemorySpendStore(monotonic), limits or spend_limits(config), monotonic=monotonic
    )
    return Services(
        config=config,
        identity=InMemoryIdentity(),
        runner=TurnRunner(
            router=build_router(config, fake_script=script).with_guard(gate),
            prompts=config.prompts,
            stores=turns.store,
            tracer=NullTracer(),
            config_hash=config.config_hash,
            max_message_chars=config.settings.max_message_chars,
            memory=Memory(InMemoryMemory().store),
        ),
        tracer=NullTracer(),
        signer=SessionSigner(config.settings.session_secret.get_secret_value()),
        quotas=Quotas(turns, quota_limits(config.settings)),
        gate=gate,
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
        "prompt": "answer@3",
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
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5, 6]
    assert [e["event"]["type"] for e in events] == [
        "model_call",
        "intent",
        "step",
        "model_call",
        "step",
        "quota",
    ]
    # The quota after the turn is stored with it, so a reload shows the same number (R.8).
    quota = events[5]["event"]
    assert quota["limit_usd"] == 2.5
    assert 0 < quota["used_usd"] < 2.5
    assert quota["remaining_usd"] == pytest.approx(2.5 - quota["used_usd"])
    assert [e["event"]["step"] for e in events if e["event"]["type"] == "step"] == [
        "understand",
        "answer",
    ]
    assert events[3]["event"]["usage"]["cost_usd"] > 0
    # Every stored event was streamed as it was persisted, with the same seq and body; each
    # step's start came first (ADR-0029).
    live = [d for n, d in got if n == "turn.event"]
    assert live == [{"seq": e["seq"], "event": e["event"]} for e in events]
    started = [d["step"] for n, d in got if n == "step.started"]
    assert started == ["understand", "answer"]
    order = [n for n, _ in got if n in {"step.started", "turn.event", "token"}]
    assert order.index("token") > order.index("step.started", 1)  # reply inside the answer step


async def test_usage_is_read_only_and_moves_with_each_turn(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/me/usage")).status_code == 401
    ws = await login(client)
    before = (await client.get("/v1/me/usage")).json()
    assert before == {
        "tier": "standard",
        "limit_usd": 2.5,
        "used_usd": 0.0,
        "remaining_usd": 2.5,
        "used_tokens": 0,
        "read_only": False,
        "read_only_reason": None,
        "read_only_message": None,
    }

    response = await client.post(f"/v1/workspaces/{ws}/turns", json={"message": "hello"})
    name, completed = frames(response.text)[-1]
    assert name == "turn.completed"
    usage = completed["usage"]
    raw = usage["input_tokens"] + usage["output_tokens"] + usage["cached_input_tokens"]
    used = usage["charged_tokens"]
    # The quota counts weighted tokens: fake-chat weighs half the baseline.
    assert 0 < used < raw
    quota = completed["quota"]
    assert quota["used_tokens"] == used
    # The quota is money: what the turn's calls cost, off the tier's allowance.
    assert usage["cost_usd"] > 0
    assert quota["used_usd"] == pytest.approx(usage["cost_usd"])
    assert quota["remaining_usd"] == pytest.approx(before["limit_usd"] - usage["cost_usd"])
    assert (await client.get("/v1/me/usage")).json() == quota


async def test_meta_lists_the_picker_with_prices_against_auto_and_who_may_pick(
    client: httpx.AsyncClient,
) -> None:
    picker = (await client.get("/v1/meta")).json()["picker"]
    assert picker["auto_label"] == "Auto"
    assert 0.004 < picker["auto_usd_per_turn"] < 0.008  # a recall turn on the economy routing
    by_id = {c["id"]: c for c in picker["choices"]}
    assert list(by_id) == [
        "openai:gpt-6-luna",
        "openai:gpt-5.4-mini",
        "anthropic:claude-haiku-4-5",
        "anthropic:claude-sonnet-5",
    ]
    assert (
        by_id["openai:gpt-6-luna"]["relative_price"]
        < 1
        < by_id["openai:gpt-5.4-mini"]["relative_price"]
    )
    assert (
        by_id["anthropic:claude-sonnet-5"]["relative_price"]
        > by_id["anthropic:claude-haiku-4-5"]["relative_price"]
    )
    assert by_id["openai:gpt-6-luna"]["tiers"] == ["standard", "premium"]
    assert by_id["anthropic:claude-sonnet-5"]["tiers"] == ["premium"]
    assert all(c["simulated"] and c["available"] for c in picker["choices"])
    assert "default" not in picker  # no pick is Auto: the same for everyone


async def test_a_pick_is_served_on_that_model_for_every_chat_step_and_is_priced_as_it(
    client: httpx.AsyncClient,
) -> None:
    ws = await login(client)
    body = {"message": "what should I cook tonight?", "model": "openai:gpt-5.4-mini"}
    response = await client.post(f"/v1/workspaces/{ws}/turns", json=body)
    name, completed = frames(response.text)[-1]
    assert name == "turn.completed"
    turn = completed["turn"]
    # A question: recall embeds it, and embeddings keep their own route (ADR-0030).
    chat = {step: m["model"] for step, m in turn["models"].items() if step != "embed"}
    assert set(chat.values()) == {"gpt-5.4-mini"}
    events = (await client.get(f"/v1/turns/{turn['id']}/events")).json()["events"]
    calls = [e["event"] for e in events if e["event"]["type"] == "model_call"]
    assert calls
    for call in calls:
        if call["step"] == "embed":
            continue
        u = call["usage"]
        # Input, cached input and output at gpt-5.4-mini's prices: $0.75 / $0.075 / $4.50 per 1M.
        cost = (
            u["input_tokens"] * 0.75 + u["cached_input_tokens"] * 0.075 + u["output_tokens"] * 4.5
        ) / 1_000_000
        assert u["cost_usd"] == pytest.approx(cost, abs=1e-7)
    assert completed["quota"]["used_usd"] == pytest.approx(completed["usage"]["cost_usd"])


async def test_a_pick_the_tier_is_not_offered_is_refused_before_the_turn_starts(
    client: httpx.AsyncClient,
) -> None:
    ws = await login(client)
    refused = await client.post(
        f"/v1/workspaces/{ws}/turns",
        json={"message": "hi", "model": "anthropic:claude-sonnet-5"},  # premium only
    )
    assert refused.status_code == 422
    assert "isn't available on your plan" in refused.json()["error"]["message"]
    assert (await client.get(f"/v1/workspaces/{ws}/turns")).json()["items"] == []


async def test_an_upgrade_offers_the_dearer_model_on_the_next_turn(
    base_env: dict[str, str],
) -> None:
    services = _services(base_env)
    app = create_app(services=services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        ws = await login(c)
        (user_id,) = services.identity.users  # type: ignore[attr-defined]
        user = services.identity.users[user_id]  # type: ignore[attr-defined]
        services.identity.users[user_id] = user.model_copy(update={"tier": Tier.PREMIUM})  # type: ignore[attr-defined]
        body = {"message": "hi", "model": "anthropic:claude-sonnet-5"}
        ok = await c.post(f"/v1/workspaces/{ws}/turns", json=body)
        assert ok.status_code == 200
        assert frames(ok.text)[-1][0] == "turn.completed"
        services.identity.users[user_id] = user.model_copy(update={"tier": Tier.GUEST})  # type: ignore[attr-defined]
        for model in ("openai:gpt-6-luna", "anthropic:claude-sonnet-5"):
            refused = await c.post(
                f"/v1/workspaces/{ws}/turns", json={"message": "hi", "model": model}
            )
            assert refused.status_code == 422  # a guest has Auto only


async def test_picking_a_model_not_on_the_list_is_refused(client: httpx.AsyncClient) -> None:
    ws = await login(client)
    for model in ("anthropic:claude-2", "not-a-ref"):
        response = await client.post(
            f"/v1/workspaces/{ws}/turns", json={"message": "hi", "model": model}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"


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
    assert frame_events == {
        "turn.started",
        "token",
        "step.started",
        "turn.event",
        "turn.completed",
        "turn.failed",
    }
    steps = schema["components"]["schemas"]["AgentStep"]["enum"]
    assert {"understand", "guard", "save", "answer", "undo", "confirm", "search", "fetch"} <= set(
        steps
    )


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

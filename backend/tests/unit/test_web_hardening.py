"""R.11: what the API does for the browser's safety. Headers on every response; no CORS; a write
must be JSON and must not come from another site; bodies have a size limit; the interactive docs
and the dev helpers exist only where they belong; and on staging, dev sign-in needs the access
code (compared in constant time, attempts limited, one user per email)."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from secondmind.api import Services, create_app
from tests.unit.test_api import _services, frames

CODE = "open-sesame-42"
SECURE = {
    "SESSION_SECRET": "a-long-random-staging-secret-value-0123456789",
    "SESSION_COOKIE_SECURE": "true",
}
LIVE = {
    "MODEL_PROVIDER_MODE": "live",
    "ANTHROPIC_API_KEY": "sk-ant-not-a-real-key-for-the-test",
    "OPENAI_API_KEY": "sk-not-a-real-key-for-the-test-000000",
}


def client_for(services: Services, scheme: str = "http") -> httpx.AsyncClient:
    app = create_app(services=services)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=f"{scheme}://web.test"
    )


@pytest.fixture
async def dev(base_env: dict[str, str]) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(_services(base_env)) as c:
        yield c


# ------------------------------------------------------------------ headers


async def test_every_response_carries_the_security_headers(dev: httpx.AsyncClient) -> None:
    for path in ("/healthz", "/v1/meta", "/v1/me", "/no/such/route"):
        headers = (await dev.get(path)).headers
        assert headers["x-content-type-options"] == "nosniff", path
        assert headers["referrer-policy"] == "no-referrer", path
        assert headers["x-frame-options"] == "DENY", path
        csp = headers["content-security-policy"]
        assert "default-src 'none'" in csp
        assert "frame-ancestors 'none'" in csp
    assert (await dev.get("/v1/meta")).headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in (await dev.get("/healthz")).headers  # plain HTTP


async def test_hsts_is_sent_when_the_site_is_served_over_https(base_env: dict[str, str]) -> None:
    services = _services(base_env, SESSION_COOKIE_SECURE="true")
    async with client_for(services) as c:
        hsts = (await c.get("/healthz")).headers["strict-transport-security"]
    assert "max-age=31536000" in hsts
    assert "includeSubDomains" in hsts


async def test_the_interactive_docs_keep_their_own_policy_in_development(
    dev: httpx.AsyncClient,
) -> None:
    docs = await dev.get("/docs/api")
    assert docs.status_code == 200
    assert "content-security-policy" not in docs.headers  # Swagger UI loads its own assets
    assert docs.headers["x-content-type-options"] == "nosniff"


async def test_the_docs_and_schema_are_off_in_production(base_env: dict[str, str]) -> None:
    services = _services(
        base_env,
        **(LIVE | SECURE | {"ENV": "production", "DEV_AUTH": "false"}),
    )
    async with client_for(services, "https") as c:
        assert (await c.get("/docs/api")).status_code == 404
        assert (await c.get("/openapi.json")).status_code == 404
        assert (await c.get("/healthz")).status_code == 200


# ------------------------------------------------------------------ CORS


async def test_there_is_no_cors_a_preflight_gets_no_permission(dev: httpx.AsyncClient) -> None:
    preflight = await dev.options(
        "/v1/workspaces/00000000-0000-0000-0000-000000000000/turns",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code in (403, 404, 405)
    assert not any(h.lower().startswith("access-control-") for h in preflight.headers)
    plain = await dev.get("/v1/meta", headers={"Origin": "https://evil.example"})
    assert not any(h.lower().startswith("access-control-") for h in plain.headers)


# ------------------------------------------------------------------ CSRF


async def _login(c: httpx.AsyncClient) -> str:
    response = await c.post("/v1/auth/dev-login")
    assert response.status_code == 200
    return str(response.json()["workspaces"][0]["id"])


async def test_a_write_from_another_site_is_refused_before_it_reaches_a_handler(
    dev: httpx.AsyncClient,
) -> None:
    ws = await _login(dev)
    url = f"/v1/workspaces/{ws}/turns"
    body: dict[str, Any] = {"message": "hello"}
    for headers in (
        {"Origin": "https://evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "http://web.test.evil.example"},
    ):
        refused = await dev.post(url, json=body, headers=headers)
        assert refused.status_code == 403, headers
        assert refused.json()["error"]["code"] == "cross_site_request"
    assert (await dev.get(f"/v1/workspaces/{ws}/turns")).json()["items"] == []  # nothing ran


async def test_a_write_from_this_site_or_from_no_browser_at_all_goes_through(
    dev: httpx.AsyncClient,
) -> None:
    ws = await _login(dev)
    url = f"/v1/workspaces/{ws}/turns"
    for headers in (
        {"Origin": "http://web.test", "Sec-Fetch-Site": "same-origin"},
        {"Origin": "http://web.test"},
        {},  # curl, a script, the E2E runner: not a browser, so not a CSRF
    ):
        ok = await dev.post(url, json={"message": "hi"}, headers=headers)
        assert ok.status_code == 200, headers
        assert frames(ok.text)[-1][0] == "turn.completed"


async def test_another_front_end_can_be_allowed_by_name(base_env: dict[str, str]) -> None:
    services = _services(base_env, ALLOWED_ORIGINS="https://app.example.test")
    async with client_for(services) as c:
        ws = await _login(c)
        ok = await c.post(
            f"/v1/workspaces/{ws}/turns",
            json={"message": "hi"},
            headers={"Origin": "https://app.example.test"},
        )
        assert ok.status_code == 200
        bad = await c.post(
            f"/v1/workspaces/{ws}/turns",
            json={"message": "hi"},
            headers={"Origin": "https://other.example.test"},
        )
        assert bad.status_code == 403


async def test_a_write_that_is_not_json_is_refused_so_a_html_form_cannot_send_one(
    dev: httpx.AsyncClient,
) -> None:
    ws = await _login(dev)
    for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
        refused = await dev.post(
            f"/v1/workspaces/{ws}/turns",
            content=b'{"message": "hello"}',
            headers={"Content-Type": content_type},
        )
        assert refused.status_code == 415, content_type
        assert refused.json()["error"]["code"] == "unsupported_media_type"


async def test_a_write_with_no_body_needs_no_content_type(dev: httpx.AsyncClient) -> None:
    await _login(dev)
    assert (await dev.post("/v1/auth/logout")).status_code == 204


async def test_reads_are_never_refused_for_where_they_came_from(dev: httpx.AsyncClient) -> None:
    await _login(dev)
    ok = await dev.get("/v1/me", headers={"Origin": "https://evil.example"})
    assert ok.status_code == 200  # the browser won't hand a cross-site page the response


# ------------------------------------------------------------------ size


async def test_a_body_over_the_limit_is_refused_by_its_declared_size(
    base_env: dict[str, str],
) -> None:
    services = _services(base_env, MAX_REQUEST_BYTES="2048")
    async with client_for(services) as c:
        ws = await _login(c)
        big = await c.post(f"/v1/workspaces/{ws}/turns", json={"message": "x" * 5000})
        assert big.status_code == 413
        assert big.json()["error"]["code"] == "payload_too_large"
        small = await c.post(f"/v1/workspaces/{ws}/turns", json={"message": "x" * 100})
        assert small.status_code == 200


async def test_a_body_streamed_past_the_limit_is_refused_too(base_env: dict[str, str]) -> None:
    services = _services(base_env, MAX_REQUEST_BYTES="2048")
    async with client_for(services) as c:
        ws = await _login(c)

        async def chunks() -> AsyncIterator[bytes]:  # no Content-Length: chunked
            yield b'{"message": "'
            for _ in range(10):
                yield b"x" * 500
            yield b'"}'

        big = await c.post(
            f"/v1/workspaces/{ws}/turns",
            content=chunks(),
            headers={"Content-Type": "application/json"},
        )
        assert big.status_code == 413


async def test_the_message_length_limit_still_applies_under_the_size_limit(
    base_env: dict[str, str],
) -> None:
    services = _services(base_env, MAX_MESSAGE_CHARS="20")
    async with client_for(services) as c:
        ws = await _login(c)
        long = await c.post(f"/v1/workspaces/{ws}/turns", json={"message": "y" * 21})
        assert long.status_code == 422


# ------------------------------------------------------------------ what exists where


async def test_the_dev_helpers_exist_only_with_dev_auth(base_env: dict[str, str]) -> None:
    async with client_for(_services(base_env, DEV_AUTH="false")) as c:
        assert (await c.post("/v1/dev/seed-recall")).status_code == 404
        assert (await c.post("/v1/auth/dev-login")).status_code == 404
    async with client_for(_services(base_env)) as c:
        assert (await c.post("/v1/dev/seed-recall")).status_code == 401  # there, behind sign-in


# ------------------------------------------------------------------ staging access


def staging(base_env: dict[str, str], **changes: str) -> Services:
    env = LIVE | SECURE | {"ENV": "staging", "DEV_AUTH": "true", "STAGING_ACCESS_CODE": CODE}
    return _services(base_env, **(env | changes))


async def test_staging_dev_login_needs_the_access_code(base_env: dict[str, str]) -> None:
    async with client_for(staging(base_env), "https") as c:
        meta = (await c.get("/v1/meta")).json()
        assert meta["access_code_required"] is True
        for body in (
            {},
            {"email": "ada@example.test"},
            {"email": "ada@example.test", "access_code": "nope"},
        ):
            refused = await c.post("/v1/auth/dev-login", json=body)
            assert refused.status_code == 401, body
            assert refused.json()["error"]["message"] == "That access code isn't right."
        assert (await c.get("/v1/me")).status_code == 401  # no session came out of a refusal
        ok = await c.post(
            "/v1/auth/dev-login", json={"email": "ada@example.test", "access_code": CODE}
        )
        assert ok.status_code == 200
        assert (await c.get("/v1/me")).json()["user"]["email"] == "ada@example.test"


async def test_each_code_holder_gets_their_own_user_and_workspace(base_env: dict[str, str]) -> None:
    async with client_for(staging(base_env), "https") as c:
        ada = (
            await c.post(
                "/v1/auth/dev-login", json={"email": "ada@example.test", "access_code": CODE}
            )
        ).json()
        grace = (
            await c.post(
                "/v1/auth/dev-login", json={"email": "grace@example.test", "access_code": CODE}
            )
        ).json()
        assert ada["user"]["id"] != grace["user"]["id"]
        assert ada["workspaces"][0]["id"] != grace["workspaces"][0]["id"]
        # Grace's session can't open Ada's workspace: isolation still applies.
        opened = await c.get(f"/v1/workspaces/{ada['workspaces'][0]['id']}/turns")
        assert opened.status_code == 404


async def test_staging_access_attempts_are_rate_limited_per_address(
    base_env: dict[str, str],
) -> None:
    async with client_for(staging(base_env, LOGIN_ATTEMPTS_PER_MINUTE="3"), "https") as c:
        statuses = [
            (
                await c.post(
                    "/v1/auth/dev-login", json={"email": "a@example.test", "access_code": f"g{i}"}
                )
            ).status_code
            for i in range(5)
        ]
        assert statuses == [401, 401, 401, 429, 429]
        limited = await c.post(
            "/v1/auth/dev-login", json={"email": "a@example.test", "access_code": CODE}
        )
        assert limited.status_code == 429  # even the right code waits: guessing gets nothing
        assert int(limited.headers["retry-after"]) >= 1


async def test_the_access_code_is_compared_in_constant_time(base_env: dict[str, str]) -> None:
    from secondmind.api.routes import auth  # noqa: PLC0415

    assert auth.hmac.compare_digest.__module__ in ("hmac", "_operator", "operator", "_hashlib")
    source = auth._check_access_code.__code__.co_names
    assert "compare_digest" in source
    async with client_for(staging(base_env), "https") as c:
        near = await c.post(
            "/v1/auth/dev-login", json={"email": "a@example.test", "access_code": CODE[:-1]}
        )
        assert near.status_code == 401


async def test_development_needs_no_access_code(dev: httpx.AsyncClient) -> None:
    assert (await dev.get("/v1/meta")).json()["access_code_required"] is False
    assert (await dev.post("/v1/auth/dev-login")).status_code == 200

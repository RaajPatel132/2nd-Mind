"""Shared fixtures. Unit tests are hermetic: they never read the developer's environment.

Every test not marked ``live`` runs behind a guard that refuses any HTTP request to a model
provider (R.1): keys in ``.env`` or the shell can never turn ``make test`` or
``make test-int`` into paid calls.
"""

import contextlib
import importlib
from collections.abc import Iterator
from types import ModuleType
from typing import Any

import pytest

BASE_ENV: dict[str, str] = {
    "ENV": "test",
    "DATABASE_URL": "postgresql+asyncpg://app:app@localhost:5432/secondmind",
    "REDIS_URL": "redis://localhost:6379/0",
    "SESSION_SECRET": "test-secret-test-secret-test-secret-000",
    "MODEL_PROVIDER_MODE": "auto",
    "TRACING_ENABLED": "false",
}

PROVIDER_HOSTS = frozenset({"api.anthropic.com", "api.openai.com"})
# The provider SDKs send through httpx2; anything else here may use httpx.
_HTTP_LIBS: list[ModuleType] = []
for _name in ("httpx2", "httpx"):
    with contextlib.suppress(ImportError):
        _HTTP_LIBS.append(importlib.import_module(_name))


class ProviderCallBlockedError(RuntimeError):
    """A test that isn't marked ``live`` tried to reach a model provider."""


@pytest.fixture
def base_env() -> dict[str, str]:
    return dict(BASE_ENV)


@pytest.fixture(autouse=True)
def _no_provider_calls(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    if request.node.get_closest_marker("live") is not None:
        yield
        return
    # The real network transports only: tests that mock a transport keep working.
    for lib in _HTTP_LIBS:
        monkeypatch.setattr(
            lib.AsyncHTTPTransport,
            "handle_async_request",
            _guarded(lib.AsyncHTTPTransport.handle_async_request),
        )
        monkeypatch.setattr(
            lib.HTTPTransport, "handle_request", _guarded_sync(lib.HTTPTransport.handle_request)
        )
    yield


def _guarded(handle: Any) -> Any:
    async def guarded(self: Any, req: Any) -> Any:
        if req.url.host in PROVIDER_HOSTS:
            raise ProviderCallBlockedError(f"a non-live test called {req.url.host}")
        return await handle(self, req)

    return guarded


def _guarded_sync(handle: Any) -> Any:
    def guarded(self: Any, req: Any) -> Any:
        if req.url.host in PROVIDER_HOSTS:
            raise ProviderCallBlockedError(f"a non-live test called {req.url.host}")
        return handle(self, req)

    return guarded

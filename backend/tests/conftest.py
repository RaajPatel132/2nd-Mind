"""Shared fixtures. Unit tests are hermetic: they never read the developer's environment."""

import pytest

BASE_ENV: dict[str, str] = {
    "ENV": "test",
    "DATABASE_URL": "postgresql+asyncpg://app:app@localhost:5432/secondmind",
    "REDIS_URL": "redis://localhost:6379/0",
    "SESSION_SECRET": "test-secret-test-secret-test-secret-000",
    "MODEL_PROVIDER_MODE": "auto",
    "TRACING_ENABLED": "false",
}


@pytest.fixture
def base_env() -> dict[str, str]:
    return dict(BASE_ENV)

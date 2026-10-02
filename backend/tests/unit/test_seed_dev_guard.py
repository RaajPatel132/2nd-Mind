"""`make seed-dev` empties a workspace, so it runs only where open dev sign-in does. Code sign-in
(DEV_AUTH with an access code) is on in production, and a tester's memory lives there."""

import asyncio

import pytest

from secondmind.evals.__main__ import _seed_dev
from tests.unit.test_web_hardening import CODE, LIVE, SECURE


def _no_runtime(*_: object, **__: object) -> None:
    raise AssertionError("seed-dev reached the database although it must have refused")


def test_it_refuses_in_production_even_with_dev_auth_on(
    monkeypatch: pytest.MonkeyPatch, base_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    env = base_env | LIVE | SECURE | {"ENV": "production", "DEV_AUTH": "true", "ACCESS_CODE": CODE}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DATABASE_MIGRATION_URL", "postgresql+asyncpg://owner@localhost/prod")
    monkeypatch.setattr("secondmind.agent.adapters.build_runtime", _no_runtime)
    assert asyncio.run(_seed_dev(None, "http://localhost:8080")) == 2
    seen = capsys.readouterr()
    assert "refusing to reset a workspace" in seen.err
    assert seen.out == ""


def test_in_development_it_gets_as_far_as_asking_for_the_owner_connection(
    monkeypatch: pytest.MonkeyPatch, base_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    for key, value in (base_env | {"ENV": "development", "DEV_AUTH": "true"}).items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("DATABASE_MIGRATION_URL", raising=False)
    monkeypatch.setattr("secondmind.agent.adapters.build_runtime", _no_runtime)
    assert asyncio.run(_seed_dev(None, "http://localhost:8080")) == 2
    assert "DATABASE_MIGRATION_URL is needed" in capsys.readouterr().err

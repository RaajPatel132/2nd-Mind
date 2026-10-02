"""ADR-0032's admin CLI: the kill switch can be set and cleared back to the environment's value,
and a command's own failures go to stderr, where a script looks for them."""

import pytest

from secondmind.api import admin
from secondmind.core import NotFoundError
from secondmind.metering.adapters import InMemorySpendStore


class SharedStore(InMemorySpendStore):
    """The one in-memory store every command of a test talks to, as Redis would be."""

    async def aclose(self) -> None:
        return None


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch, base_env: dict[str, str]) -> SharedStore:
    for key, value in base_env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    store = SharedStore()
    monkeypatch.setattr(admin.RedisSpendStore, "from_url", classmethod(lambda cls, url: store))
    return store


def run(args: list[str]) -> int:
    return admin.main(args)


def test_the_runtime_flag_overrides_the_environment_until_it_is_cleared(
    redis: SharedStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("KILL_SWITCH", "false")
    assert run(["kill-switch", "on"]) == 0
    assert "kill switch is ON (from the runtime flag)" in capsys.readouterr().out

    assert run(["kill-switch", "clear"]) == 0
    out = capsys.readouterr().out
    assert "kill switch is off (from KILL_SWITCH in the environment)" in out

    assert run(["kill-switch", "status"]) == 0
    assert "from KILL_SWITCH in the environment" in capsys.readouterr().out


def test_clearing_gives_the_environments_value_even_when_that_is_on(
    redis: SharedStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("KILL_SWITCH", "true")
    assert run(["kill-switch", "off"]) == 0
    assert "kill switch is off (from the runtime flag)" in capsys.readouterr().out
    assert run(["kill-switch", "clear"]) == 0
    assert "kill switch is ON (from KILL_SWITCH in the environment)" in capsys.readouterr().out


def test_a_set_tier_that_cannot_run_says_so_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_MIGRATION_URL", raising=False)
    assert run(["set-tier", "ada@example.test", "premium"]) == 2
    seen = capsys.readouterr()
    assert "DATABASE_MIGRATION_URL is required" in seen.err
    assert seen.out == ""


def test_an_unknown_person_is_reported_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Gone:
        def __init__(self, db: object) -> None: ...

        async def set_tier(self, **_: object) -> None:
            raise NotFoundError("no one has the email nobody@example.test")

    class Db:
        def __init__(self, url: str, pool_size: int = 1) -> None: ...

        async def dispose(self) -> None:
            return None

    monkeypatch.setenv("DATABASE_MIGRATION_URL", "postgresql+asyncpg://owner@localhost/none")
    monkeypatch.setattr(admin, "SqlAdminStore", Gone)
    monkeypatch.setattr(admin, "Database", Db)
    assert run(["set-tier", "nobody@example.test", "premium"]) == 1
    seen = capsys.readouterr()
    assert "no one has the email" in seen.err
    assert seen.out == ""

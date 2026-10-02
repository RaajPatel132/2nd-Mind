"""ADR-0032's admin CLI: a command's own failures go to stderr, where a script looks for them."""

import pytest

from secondmind.api import admin
from secondmind.core import NotFoundError


def run(args: list[str]) -> int:
    return admin.main(args)


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

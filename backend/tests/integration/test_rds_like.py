"""R.12: migrations behave on a managed Postgres. RDS's master user is not a superuser: it can
create roles and databases and (for trusted extensions) extensions, and nothing more. These tests
run the bootstrap and every migration as such an owner, check that a second migrate started at the
same time waits on the advisory lock, and that a lost database connection heals (a failover)."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer

from secondmind.memory.adapters import SCHEMA_HEAD, Database, ensure_app_role
from secondmind.memory.adapters.bootstrap import MIGRATION_LOCK_KEY
from tests.integration.conftest import APP_PASSWORD, APP_ROLE, PgUrls

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
OWNER_PASSWORD = "owner-test-password"


@pytest.fixture
async def rds_like() -> AsyncIterator[str]:
    """A cluster of its own (roles are cluster-wide) with a database owned by a login role that
    has CREATEROLE and nothing else, the pgvector extension created beforehand by the superuser
    (as RDS's master user would)."""
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        superuser = pg.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+asyncpg://"
        )
        owner, database = "rds_owner", "rds"
        admin = create_async_engine(superuser, isolation_level="AUTOCOMMIT")
        async with admin.connect() as conn:
            await conn.execute(
                text(
                    f"CREATE ROLE {owner} LOGIN PASSWORD '{OWNER_PASSWORD}' "
                    "NOSUPERUSER NOBYPASSRLS CREATEROLE"
                )
            )
            await conn.execute(text(f"CREATE DATABASE {database} OWNER {owner}"))
        await admin.dispose()
        inside = create_async_engine(
            make_url(superuser).set(database=database).render_as_string(hide_password=False),
            isolation_level="AUTOCOMMIT",
        )
        async with inside.connect() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await inside.dispose()
        yield (
            make_url(superuser)
            .set(username=owner, password=OWNER_PASSWORD, database=database)
            .render_as_string(hide_password=False)
        )


def _upgrade(url: str) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")


async def test_bootstrap_and_every_migration_run_as_a_non_superuser_owner(rds_like: str) -> None:
    engine = create_async_engine(rds_like)
    async with engine.connect() as conn:
        assert (
            await conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"))
        ).scalar() is False
    await engine.dispose()

    app_role = f"{APP_ROLE}_{uuid.uuid4().hex[:6]}"
    await ensure_app_role(rds_like, app_role, APP_PASSWORD)
    await asyncio.to_thread(_upgrade, rds_like)

    app_url = make_url(rds_like).set(username=app_role, password=APP_PASSWORD)
    db = Database(app_url.render_as_string(hide_password=False))
    try:
        assert await db.schema_revision() == SCHEMA_HEAD
        check = await db.role_check()
        assert check.safe, check
    finally:
        await db.dispose()


async def test_a_second_migrate_waits_on_the_advisory_lock(rds_like: str) -> None:
    holder = create_async_engine(rds_like)
    async with holder.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
        second = asyncio.create_task(asyncio.to_thread(_upgrade, rds_like))
        await asyncio.sleep(3)
        assert not second.done(), "the second migrate did not wait for the lock"
        await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
        await conn.commit()
        await asyncio.wait_for(second, timeout=120)
    await holder.dispose()


async def test_a_lost_database_connection_heals_on_the_next_request(
    app_db: Database, pg_urls: PgUrls
) -> None:
    """A Postgres restart or an RDS failover drops every connection at once. The pool checks a
    connection before using it, so the next request succeeds instead of failing once."""
    assert await app_db.schema_revision() == SCHEMA_HEAD  # the pool now holds live connections
    admin = create_async_engine(pg_urls.owner, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                " WHERE usename = :u AND pid <> pg_backend_pid()"
            ),
            {"u": APP_ROLE},
        )
    await admin.dispose()
    assert await app_db.schema_revision() == SCHEMA_HEAD

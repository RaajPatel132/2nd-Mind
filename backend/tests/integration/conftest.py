"""Integration fixtures: real Postgres (pgvector) and Redis in containers (testcontainers).

Migrations run as the owner, then the app connects as the non-owner login role, exactly as in
production. One container per session; tests isolate themselves with fresh workspaces.
"""

import asyncio
import os
import shutil
import subprocess
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from secondmind.auth.adapters import SqlIdentityStore
from secondmind.memory.adapters import Database, ensure_app_role

BACKEND = Path(__file__).resolve().parents[2]
APP_ROLE = "secondmind_app"
APP_PASSWORD = "app-test-password"


def _configure_docker_host() -> None:
    """Point testcontainers at the active Docker context (colima, Docker Desktop, CI)."""
    if os.environ.get("DOCKER_HOST") or not shutil.which("docker"):
        return
    result = subprocess.run(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    host = result.stdout.strip()
    if host.startswith("unix://") and host != "unix:///var/run/docker.sock":
        os.environ["DOCKER_HOST"] = host
        # Inside the VM the daemon socket is at the standard path (used by the Ryuk reaper).
        os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")


_configure_docker_host()


@dataclass(frozen=True)
class PgUrls:
    owner: str
    app: str


def _asyncpg(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


def run_migrations(owner_url: str, revision: str = "head") -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["url"] = owner_url
    command.upgrade(cfg, revision)


@pytest.fixture(scope="session")
def pg_urls() -> Iterator[PgUrls]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        owner = _asyncpg(pg.get_connection_url())
        asyncio.run(ensure_app_role(owner, APP_ROLE, APP_PASSWORD))
        run_migrations(owner)
        app = make_url(owner).set(username=APP_ROLE, password=APP_PASSWORD)
        yield PgUrls(owner=owner, app=app.render_as_string(hide_password=False))


@pytest.fixture(scope="session")
async def app_db(pg_urls: PgUrls) -> AsyncIterator[Database]:
    db = Database(pg_urls.app, pool_size=5)
    yield db
    await db.dispose()


@pytest.fixture(scope="session")
async def owner_db(pg_urls: PgUrls) -> AsyncIterator[Database]:
    db = Database(pg_urls.owner, pool_size=2)
    yield db
    await db.dispose()


@pytest.fixture(scope="session")
def identity(app_db: Database) -> SqlIdentityStore:
    return SqlIdentityStore(app_db)


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as redis:
        host = redis.get_container_host_ip()
        port = redis.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"

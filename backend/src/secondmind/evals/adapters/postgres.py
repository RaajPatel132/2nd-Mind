"""A throwaway Postgres for eval runs that need the real SQL tools (the recall suite).

Migrations run as the owner and the suite connects as the non-owner app role under RLS, as in
production. testcontainers is a dev dependency: this runs host-side only, never in the image.
"""

import asyncio
import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from secondmind.config import DEFAULT_RESOURCES_DIR
from secondmind.memory.adapters import ensure_app_role

APP_ROLE = "secondmind_app"
APP_PASSWORD = "app-eval-password"  # noqa: S105 - a throwaway container's password


def configure_docker_host() -> None:
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
        os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")


def _asyncpg(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


def migrate(owner_url: str, resources: Path = DEFAULT_RESOURCES_DIR) -> None:
    # No ini file: the migrations' env.py then leaves logging alone (no per-revision INFO lines).
    cfg = Config()
    cfg.set_main_option("script_location", str(resources / "migrations"))
    cfg.attributes["url"] = owner_url
    command.upgrade(cfg, "head")


@contextmanager
def throwaway_postgres() -> Iterator[str]:
    """A migrated pgvector Postgres 16; yields the app role's URL."""
    from testcontainers.postgres import PostgresContainer  # noqa: PLC0415 - dev dependency

    configure_docker_host()
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        owner = _asyncpg(pg.get_connection_url())
        asyncio.run(ensure_app_role(owner, APP_ROLE, APP_PASSWORD))
        migrate(owner)
        app = make_url(owner).set(username=APP_ROLE, password=APP_PASSWORD)
        yield app.render_as_string(hide_password=False)

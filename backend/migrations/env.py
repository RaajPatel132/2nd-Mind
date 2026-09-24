"""Alembic environment. Migrations run as the schema owner (DATABASE_MIGRATION_URL); the app
itself connects as a non-owner role that row-level security applies to."""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import secondmind.agent.adapters
import secondmind.auth.adapters
import secondmind.metering.adapters  # noqa: F401 - register tables on the metadata
from secondmind.memory.adapters import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    url = config.attributes.get("url") or os.environ.get("DATABASE_MIGRATION_URL", "")
    if not url:
        raise SystemExit("DATABASE_MIGRATION_URL is required to run migrations")
    return str(url)


def _include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    # Postgres-generated / role objects are managed by the migrations directly.
    return not (type_ == "table" and name == "alembic_version")


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

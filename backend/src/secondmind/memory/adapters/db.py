"""Postgres access: the engine, and sessions that are always workspace-scoped.

``Database.workspace(scope)`` is the only way to get a session for workspace-owned data. It
opens a transaction and sets ``app.workspace_id`` for that transaction only, which the RLS
policies filter on.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from secondmind.core import WorkspaceScope
from secondmind.memory import WORKSPACE_SETTING

# Latest migration revision; readiness fails until the database is at it.
SCHEMA_HEAD = "0002"

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


@dataclass(frozen=True, slots=True)
class RoleCheck:
    role: str
    is_superuser: bool
    bypasses_rls: bool
    owns_tables: bool

    @property
    def safe(self) -> bool:
        return not (self.is_superuser or self.bypasses_rls or self.owns_tables)


class Database:
    def __init__(self, url: str, *, pool_size: int = 10, echo: bool = False) -> None:
        self._engine: AsyncEngine = create_async_engine(
            url, pool_size=pool_size, pool_pre_ping=True, echo=echo
        )
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def workspace(self, scope: WorkspaceScope) -> AsyncIterator[AsyncSession]:
        """A transaction bound to one workspace; commits on success, rolls back on error."""
        async with self._sessions() as session, session.begin():
            await session.execute(
                text("SELECT set_config(:name, :value, true)"),
                {"name": WORKSPACE_SETTING, "value": str(scope.workspace_id)},
            )
            yield session

    @asynccontextmanager
    async def identity(self) -> AsyncIterator[AsyncSession]:
        """A transaction for the identity tables (users, workspaces), which are not
        workspace-owned. Workspace-owned tables return nothing here: no scope is set."""
        async with self._sessions() as session, session.begin():
            yield session

    async def ping(self) -> None:
        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def schema_revision(self) -> str | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            return result.scalar_one_or_none()

    async def role_check(self) -> RoleCheck:
        """Confirm the app's role cannot bypass row-level security."""
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT current_user, r.rolsuper, r.rolbypassrls, "
                        "EXISTS (SELECT 1 FROM pg_tables t WHERE t.schemaname = 'public' "
                        "AND t.tableowner = current_user) "
                        "FROM pg_roles r WHERE r.rolname = current_user"
                    )
                )
            ).one()
        return RoleCheck(role=row[0], is_superuser=row[1], bypasses_rls=row[2], owns_tables=row[3])

    async def dispose(self) -> None:
        await self._engine.dispose()

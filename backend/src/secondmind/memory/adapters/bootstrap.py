"""Create or update the app's login role. Run as the schema owner before migrations.

The login role gets a password and membership of the ``secondmind_rw`` group role, which the
migrations grant table privileges to. It never owns tables and never has BYPASSRLS.
Usage: ``python -m secondmind.memory.adapters.bootstrap_role`` with DATABASE_MIGRATION_URL (owner),
DATABASE_URL (the app login, whose user name is used) and APP_DB_PASSWORD.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from secondmind.memory import APP_GROUP_ROLE

# One advisory lock for "changing the schema or its roles": two migrate jobs started together
# (two deploys, an ECS task retried) queue on it instead of racing on CREATE ROLE or the DDL.
MIGRATION_LOCK_KEY = 7_331_001


async def ensure_app_role(owner_url: str, app_role: str, app_password: str) -> None:
    engine = create_async_engine(owner_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
            exists = (
                await conn.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": app_role}
                )
            ).scalar_one_or_none()
            ident = conn.dialect.identifier_preparer.quote(app_role)
            group = conn.dialect.identifier_preparer.quote(APP_GROUP_ROLE)
            # Role DDL does not take bind parameters; the password is escaped as a literal.
            password = app_password.replace("'", "''")
            verb = "ALTER" if exists else "CREATE"
            await conn.execute(
                text(
                    f"{verb} ROLE {ident} LOGIN PASSWORD '{password}' "
                    "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
                )
            )
            await conn.execute(
                text(
                    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "  # noqa: S608 - constant
                    f"'{APP_GROUP_ROLE}') THEN CREATE ROLE {group} NOLOGIN; END IF; END $$"
                )
            )
            await conn.execute(text(f"GRANT {group} TO {ident}"))
    finally:
        await engine.dispose()

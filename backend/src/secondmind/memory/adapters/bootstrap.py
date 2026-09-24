"""Create or update the app's login role. Run as the schema owner before migrations.

The login role gets a password and membership of the ``secondmind_rw`` group role, which the
migrations grant table privileges to. It never owns tables and never has BYPASSRLS.
Usage: ``python -m secondmind.memory.adapters.bootstrap`` with DATABASE_MIGRATION_URL (owner),
DATABASE_URL (the app login, whose user name is used) and APP_DB_PASSWORD.
"""

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from secondmind.memory import APP_GROUP_ROLE


async def ensure_app_role(owner_url: str, app_role: str, app_password: str) -> None:
    engine = create_async_engine(owner_url)
    try:
        async with engine.begin() as conn:
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


def main() -> int:
    owner_url = os.environ.get("DATABASE_MIGRATION_URL", "")
    app_url = os.environ.get("DATABASE_URL", "")
    password = os.environ.get("APP_DB_PASSWORD", "")
    missing = [
        n
        for n, v in (
            ("DATABASE_MIGRATION_URL", owner_url),
            ("DATABASE_URL", app_url),
            ("APP_DB_PASSWORD", password),
        )
        if not v
    ]
    if missing:
        sys.stderr.write(f"bootstrap: missing {', '.join(missing)}\n")
        return 2
    role = make_url(app_url).username
    if not role:
        sys.stderr.write("bootstrap: DATABASE_URL has no user name\n")
        return 2
    asyncio.run(ensure_app_role(owner_url, role, password))
    sys.stdout.write(f"bootstrap: role {role!r} ready (member of {APP_GROUP_ROLE})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

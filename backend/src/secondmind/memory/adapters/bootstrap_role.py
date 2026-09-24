"""CLI: create or update the app's login role (run as the owner before migrations)."""

import asyncio
import os
import sys

from sqlalchemy.engine import make_url

from secondmind.memory import APP_GROUP_ROLE
from secondmind.memory.adapters.bootstrap import ensure_app_role


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

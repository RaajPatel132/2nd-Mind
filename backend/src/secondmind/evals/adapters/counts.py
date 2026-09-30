"""Row counts for an eval check, read through the workspace session (RLS applies)."""

from collections.abc import Mapping

from sqlalchemy import text

from secondmind.core import WorkspaceScope
from secondmind.memory.adapters import Database


async def workspace_counts(
    db: Database, scope: WorkspaceScope, queries: Mapping[str, str]
) -> dict[str, int]:
    """Run each ``SELECT count(*) ...`` as the workspace's own role; name -> count."""
    async with db.workspace(scope) as session:
        return {
            name: int((await session.execute(text(sql))).scalar_one())
            for name, sql in queries.items()
        }

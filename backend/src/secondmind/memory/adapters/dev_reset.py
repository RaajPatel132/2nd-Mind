"""Dev only: empty one workspace so ``make seed-dev`` can seed it again from scratch.

Runs on the owner connection (``DATABASE_MIGRATION_URL``), like migrations: the app role is not
allowed to delete turns or memories, by design. Every workspace-owned row hangs off a turn, an
entity, a category or a vocab term, so deleting those cascades to the rest. The workspace, its
members and its settings stay, and it gets a fresh ``self`` entity, as a new workspace does. The
caller refuses this outside development (DEV_AUTH).
"""

from sqlalchemy import text

from secondmind.core import WorkspaceScope
from secondmind.memory.adapters.db import Database

# Order matters: relations point at the memory they came from without a cascade, and memories
# point at entities and categories the same way.
_RESET_ORDER = ("entity_relations", "turns", "entities", "categories", "vocab_terms")


async def reset_workspace(owner_url: str, scope: WorkspaceScope) -> dict[str, int]:
    """Delete everything the workspace holds; rows deleted per table (cascades not counted)."""
    db = Database(owner_url, pool_size=1)
    try:
        deleted: dict[str, int] = {}
        async with db.workspace(scope) as session:
            for table in _RESET_ORDER:
                result = await session.execute(
                    text(f"DELETE FROM {table} WHERE workspace_id = :ws"),  # noqa: S608
                    {"ws": scope.workspace_id},
                )
                deleted[table] = int(getattr(result, "rowcount", 0) or 0)
            # What the workspaces_self_entity trigger gives a new workspace (migration 0002).
            await session.execute(
                text(
                    "INSERT INTO entities (id, workspace_id, kind, name) "
                    "VALUES (gen_random_uuid(), :ws, 'self', 'me')"
                ),
                {"ws": scope.workspace_id},
            )
        return deleted
    finally:
        await db.dispose()

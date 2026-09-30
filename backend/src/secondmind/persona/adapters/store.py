"""The database's part of the persona: ``copy_persona_template`` (migration 0007, a SECURITY DEFINER
function that accepts only a template as its source) and the catalog of workspace-owned tables the
copy must account for."""

import uuid

from sqlalchemy import text

from secondmind.memory.adapters import Database

# Every workspace-owned table is either copied or skipped on purpose. A catalog test fails when a
# new table is in neither list (S4.11).
COPIED = frozenset(
    {
        "categories",
        "vocab_terms",
        "turns",
        "entities",
        "memory_items",
        "memory_entities",
        "memory_links",
        "entity_relations",
        "triggers",
        "memory_keys",
        "link_sources",
        "conversation_keys",
    }
)
SKIPPED: dict[str, str] = {
    "turn_events": "the seed's turns carry no events, and a copy's own history starts empty",
    "usage_ledger": "the cost of loading the template is the app's, never a visitor's",
    "write_log": "a copy's seed is not something to undo; its history starts with the visitor",
    "item_versions": "history of edits starts with the visitor",
    "held_writes": "nothing is held in a template",
    "item_access": "recall bookkeeping starts fresh in every copy",
}


def workspace_tables() -> tuple[frozenset[str], dict[str, str]]:
    return COPIED, SKIPPED


class SqlPersonaStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def copy_template(
        self,
        *,
        template_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        moved_days: int,
    ) -> None:
        async with self._db.identity() as session:
            await session.execute(
                text("SELECT copy_persona_template(:t, :w, :u, :d)"),
                {"t": template_id, "w": workspace_id, "u": user_id, "d": moved_days},
            )

    async def mark_template(
        self, workspace_id: uuid.UUID, *, seed_id: str, version: int, seed_hash: str
    ) -> None:
        async with self._db.identity() as session:
            await session.execute(
                text(
                    "UPDATE workspaces SET seed_id = :s, seed_version = :v, seed_hash = :h "
                    "WHERE id = :w AND kind = 'template'"
                ),
                {"s": seed_id, "v": version, "h": seed_hash, "w": workspace_id},
            )

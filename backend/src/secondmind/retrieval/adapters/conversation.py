"""The conversation index over Postgres (``conversation_keys``, RLS-scoped)."""

import uuid
from collections.abc import Sequence

from sqlalchemy import Table, delete, insert, select

from secondmind.core import WorkspaceScope
from secondmind.memory.adapters import Database, MemoryKeyRow
from secondmind.retrieval.adapters.tables import ConversationKeyRow
from secondmind.retrieval.conversation import SaidRow

_CONV: Table = ConversationKeyRow.__table__  # type: ignore[assignment]
_KEYS: Table = MemoryKeyRow.__table__  # type: ignore[assignment]


class SqlConversationStore:
    def __init__(self, db: Database, scope: WorkspaceScope) -> None:
        self._db = db
        self._scope = scope

    async def cached_embeddings(self, hashes: Sequence[str], model: str) -> dict[str, list[float]]:
        """Vectors already computed for these texts, said or saved (the FR-14.6 cache)."""
        if not hashes:
            return {}
        out: dict[str, list[float]] = {}
        async with self._db.workspace(self._scope) as s:
            for table in (_CONV, _KEYS):
                stmt = (
                    select(table.c.content_hash, table.c.embedding)
                    .where(
                        table.c.content_hash.in_(list(hashes)),
                        table.c.embedding_model == model,
                        table.c.embedding.is_not(None),
                    )
                    .distinct(table.c.content_hash)
                )
                for row in await s.execute(stmt):
                    out.setdefault(row[0], row[1])
        return out

    async def replace_turn(self, turn_id: uuid.UUID, rows: Sequence[SaidRow]) -> None:
        async with self._db.workspace(self._scope) as s:
            await s.execute(delete(_CONV).where(_CONV.c.turn_id == turn_id))
            if rows:
                await s.execute(
                    insert(_CONV),
                    [
                        {
                            "id": r.id,
                            "workspace_id": self._scope.workspace_id,
                            "turn_id": r.turn_id,
                            "role": r.role,
                            "seq": r.seq,
                            "said_at": r.said_at,
                            "text": r.text,
                            "content_hash": r.content_hash,
                            "embedding": r.embedding,
                            "embedding_model": r.embedding_model,
                        }
                        for r in rows
                    ],
                )

    async def indexed_turns(self, turn_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
        if not turn_ids:
            return set()
        async with self._db.workspace(self._scope) as s:
            found = await s.execute(
                select(_CONV.c.turn_id).where(_CONV.c.turn_id.in_(list(turn_ids))).distinct()
            )
            return set(found.scalars())

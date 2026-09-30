"""Postgres access for saved links: ``link_sources`` and a link's chunk keys (``memory_keys`` rows
of kind ``chunk``). Workspace-scoped through RLS, like every repository."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Table, delete, func, insert, select, update

from secondmind.core import KeyKind, WorkspaceScope, utc_now
from secondmind.links.adapters.tables import LinkSourceRow
from secondmind.links.model import ChunkRow, FetchStatus, LinkSource
from secondmind.memory.adapters import Database, MemoryItemRow, MemoryKeyRow

_SOURCES: Table = LinkSourceRow.__table__  # type: ignore[assignment]
_KEYS: Table = MemoryKeyRow.__table__  # type: ignore[assignment]
_ITEMS: Table = MemoryItemRow.__table__  # type: ignore[assignment]
_FIELDS = frozenset(LinkSource.model_fields) - {
    "id",
    "workspace_id",
    "item_id",
    "turn_id",
    "created_at",
}


class SqlLinkStore:
    def __init__(self, db: Database, scope: WorkspaceScope) -> None:
        self._db = db
        self._scope = scope

    async def add(self, source: LinkSource) -> None:
        values = source.model_dump(exclude={"created_at", "updated_at"})
        values["workspace_id"] = self._scope.workspace_id
        async with self._db.workspace(self._scope) as s:
            await s.execute(insert(_SOURCES), [values])

    async def add_source(self, source: LinkSource, chunks: Sequence[ChunkRow]) -> None:
        """A link's source row and the passages of its page, together (a seeded link, S4.10)."""
        await self.add(source)
        await self.replace_chunks(source.item_id, chunks)

    async def by_item(self, item_id: uuid.UUID) -> LinkSource | None:
        async with self._db.workspace(self._scope) as s:
            row = (
                await s.execute(select(_SOURCES).where(_SOURCES.c.item_id == item_id))
            ).one_or_none()
        return None if row is None else LinkSource.model_validate(dict(row._mapping))

    async def by_items(self, item_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, LinkSource]:
        if not item_ids:
            return {}
        async with self._db.workspace(self._scope) as s:
            rows = await s.execute(select(_SOURCES).where(_SOURCES.c.item_id.in_(list(item_ids))))
            return {r.item_id: LinkSource.model_validate(dict(r._mapping)) for r in rows}

    async def by_canonical(self, canonical_url: str) -> LinkSource | None:
        """The link already saved under this address, if its item is still there (the same link
        twice reconciles to it: S4.7)."""
        stmt = (
            select(_SOURCES)
            .join(
                _ITEMS,
                (_ITEMS.c.id == _SOURCES.c.item_id)
                & (_ITEMS.c.workspace_id == _SOURCES.c.workspace_id),
            )
            .where(_SOURCES.c.canonical_url == canonical_url, _ITEMS.c.status == "active")
            .order_by(_SOURCES.c.created_at)
            .limit(1)
        )
        async with self._db.workspace(self._scope) as s:
            row = (await s.execute(stmt)).one_or_none()
        return None if row is None else LinkSource.model_validate(dict(row._mapping))

    async def update(self, item_id: uuid.UUID, **changes: Any) -> None:
        unknown = set(changes) - _FIELDS
        if unknown:
            raise ValueError(f"not a link source field: {sorted(unknown)}")
        if isinstance(changes.get("fetch_status"), FetchStatus):
            changes["fetch_status"] = changes["fetch_status"].value
        async with self._db.workspace(self._scope) as s:
            await s.execute(
                update(_SOURCES)
                .where(_SOURCES.c.item_id == item_id)
                .values(**changes, updated_at=utc_now())
            )

    async def pending_since(self, before: datetime, limit: int = 50) -> list[LinkSource]:
        """Links still waiting to be read after ``before``: the sweep re-queues these."""
        async with self._db.workspace(self._scope) as s:
            rows = await s.execute(
                select(_SOURCES)
                .where(
                    _SOURCES.c.fetch_status == FetchStatus.PENDING.value,
                    _SOURCES.c.created_at < before,
                )
                .order_by(_SOURCES.c.created_at)
                .limit(limit)
            )
            return [LinkSource.model_validate(dict(r._mapping)) for r in rows]

    # ------------------------------------------------------------------ chunks

    async def replace_chunks(self, item_id: uuid.UUID, chunks: Sequence[ChunkRow]) -> None:
        """The page's passages, as chunk keys of the item (replacing any earlier read)."""
        async with self._db.workspace(self._scope) as s:
            await s.execute(
                delete(_KEYS).where(
                    _KEYS.c.item_id == item_id, _KEYS.c.key_kind == KeyKind.CHUNK.value
                )
            )
            if chunks:
                await s.execute(
                    insert(_KEYS),
                    [
                        {
                            "id": c.id,
                            "workspace_id": self._scope.workspace_id,
                            "item_id": item_id,
                            "key_kind": KeyKind.CHUNK.value,
                            "text": c.text,
                            "content_hash": c.content_hash,
                            "embedding": c.embedding,
                            "embedding_model": c.embedding_model,
                            "position": c.position,
                        }
                        for c in chunks
                    ],
                )
            await s.execute(
                update(_SOURCES)
                .where(_SOURCES.c.item_id == item_id)
                .values(chunk_count=len(chunks))
            )

    async def chunks(self, item_id: uuid.UUID) -> list[ChunkRow]:
        async with self._db.workspace(self._scope) as s:
            rows = await s.execute(
                select(_KEYS)
                .where(_KEYS.c.item_id == item_id, _KEYS.c.key_kind == KeyKind.CHUNK.value)
                .order_by(_KEYS.c.position)
            )
            return [
                ChunkRow(
                    id=r.id,
                    position=r.position,
                    text=r.text,
                    content_hash=r.content_hash,
                    embedding=None if r.embedding is None else list(r.embedding),
                    embedding_model=r.embedding_model,
                )
                for r in rows
            ]

    async def unembedded_chunks(self, limit: int = 200) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
        """Chunk keys saved without an embedding (the provider was down), oldest first:
        (key id, item id, text). A periodic job embeds them once the provider answers again."""
        async with self._db.workspace(self._scope) as s:
            rows = await s.execute(
                select(_KEYS.c.id, _KEYS.c.item_id, _KEYS.c.text)
                .where(_KEYS.c.key_kind == KeyKind.CHUNK.value, _KEYS.c.embedding.is_(None))
                .order_by(_KEYS.c.created_at)
                .limit(limit)
            )
            return [(r.id, r.item_id, r.text) for r in rows]

    async def set_embeddings(
        self, vectors: Sequence[tuple[uuid.UUID, list[float]]], model: str
    ) -> None:
        """Give chunk keys the vectors a later job computed for them."""
        async with self._db.workspace(self._scope) as s:
            for key_id, vector in vectors:
                await s.execute(
                    update(_KEYS)
                    .where(_KEYS.c.id == key_id, _KEYS.c.key_kind == KeyKind.CHUNK.value)
                    .values(embedding=vector, embedding_model=model, updated_at=utc_now())
                )

    async def count_sources(self) -> int:
        async with self._db.workspace(self._scope) as s:
            return int((await s.execute(select(func.count()).select_from(_SOURCES))).scalar_one())

"""Retrieval keys (ADR-0020). Keys are derived data: rebuilt from the item after every commit and
every undo, never write-log ops, never in the diff. Items have no search columns of their own.

Each item gets a ``text`` key (its statement) and a ``verbal`` key (the rendered sentence), plus
a ``change`` key when it superseded another item; ``alt`` and ``cue`` keys come from the enrich
step. Embeddings are reused by ``content_hash`` (FR-14.6), so an unchanged sentence costs
nothing.
"""

import hashlib
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from secondmind.core import EntityRole, ItemStatus, KeyKind, LinkType, new_id
from secondmind.memory.records import ItemContent, ItemRecord, KeyRecord, LinkRecord
from secondmind.memory.render import RenderEntity, RenderInput, render_change, render_verbal
from secondmind.memory.store import MemoryStore, MemoryTx

# Given texts and the number of cache hits (for the model_call event), return one vector per
# text, or None when embeddings are unavailable (keys are then stored without vectors).
Embedder = Callable[[Sequence[str], int], Awaitable[list[list[float]] | None]]

DERIVED_KINDS = (KeyKind.TEXT, KeyKind.VERBAL, KeyKind.CHANGE)
ENRICHED_KINDS = (KeyKind.ALT, KeyKind.CUE)

# Relative time words go stale the day after; keys must never contain them.
RELATIVE_WORDS = re.compile(
    r"\b(yesterday|tomorrow|today|tonight|ago|"
    r"(last|next|this|coming|past)\s+(week|month|year|weekend|monday|tuesday|wednesday|"
    r"thursday|friday|saturday|sunday))\b",
    re.IGNORECASE,
)


def content_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def has_relative_time(text: str) -> bool:
    return bool(RELATIVE_WORDS.search(text))


@dataclass(frozen=True, slots=True)
class KeyReport:
    items: int
    keys: int
    embedded: int
    cache_hits: int


class KeyIndexer:
    def __init__(
        self,
        store: MemoryStore,
        *,
        timezone: str,
        embed: Embedder | None,
        model: str,
        verbal_enabled: bool = True,
    ) -> None:
        self._store = store
        self._tz = timezone
        self._embed = embed
        self._model = model
        self._verbal = verbal_enabled

    async def rebuild(
        self,
        item_ids: Sequence[uuid.UUID],
        *,
        extra: Mapping[uuid.UUID, Sequence[tuple[KeyKind, str]]] | None = None,
    ) -> KeyReport:
        extra = extra or {}
        async with self._store.transaction() as tx:
            wanted = await self._render(tx, item_ids, extra)
            hashes = sorted({content_hash(t) for keys in wanted.values() for _, t in keys})
            cached = await tx.cached_embeddings(hashes, self._model) if hashes else {}
        misses = sorted(
            {t for keys in wanted.values() for _, t in keys if content_hash(t) not in cached}
        )
        hits = len(hashes) - len({content_hash(t) for t in misses})
        vectors: dict[str, list[float]] = dict(cached)
        embedded = 0
        if (misses or hits) and self._embed is not None:
            result = await self._embed(misses, hits)
            if result is not None:
                embedded = len(result)
                vectors.update({content_hash(t): v for t, v in zip(misses, result, strict=True)})
        total = 0
        async with self._store.transaction() as tx:
            for item_id, keys in wanted.items():
                item = await tx.get_item(item_id)
                if item is None:
                    continue
                records = [
                    KeyRecord(
                        id=new_id(),
                        workspace_id=item.workspace_id,
                        item_id=item_id,
                        key_kind=kind,
                        text=text,
                        content_hash=content_hash(text),
                        embedding=vectors.get(content_hash(text)),
                        embedding_model=self._model if content_hash(text) in vectors else None,
                    )
                    for kind, text in keys
                ]
                kinds = DERIVED_KINDS + (ENRICHED_KINDS if item_id in extra else ())
                await tx.replace_keys(item_id, kinds, records)
                total += len(records)
        return KeyReport(items=len(wanted), keys=total, embedded=embedded, cache_hits=hits)

    async def pending(self, limit: int = 200) -> list[KeyRecord]:
        """Keys stored without a vector because the embedding provider was down. Passages of saved
        pages are left to the link workflow: they are embedded with their page's title."""
        async with self._store.transaction() as tx:
            return await tx.unembedded_keys(limit, exclude=(KeyKind.CHUNK,))

    async def embed_missing(self, limit: int = 200) -> int:
        """Give every such key its vector now that the provider answers (ledger 47)."""
        return await self.embed_keys(await self.pending(limit))

    async def embed_keys(self, keys: Sequence[KeyRecord]) -> int:
        """Embed exactly ``keys``. Nothing is asked of the provider when there is nothing to do,
        and the keys stay as they are (found by words) when it is still down. Returns how many
        keys got a vector."""
        if self._embed is None or not keys:
            return 0
        texts = sorted({k.text for k in keys})
        vectors = await self._embed(texts, 0)
        if vectors is None or len(vectors) != len(texts):
            return 0
        by_text = dict(zip(texts, vectors, strict=True))
        async with self._store.transaction() as tx:
            await tx.set_key_embeddings([(k.id, by_text[k.text]) for k in keys], self._model)
        return len(keys)

    async def _render(
        self,
        tx: MemoryTx,
        item_ids: Sequence[uuid.UUID],
        extra: Mapping[uuid.UUID, Sequence[tuple[KeyKind, str]]],
    ) -> dict[uuid.UUID, list[tuple[KeyKind, str]]]:
        items = {i.id: i for i in await tx.get_items(list(dict.fromkeys(item_ids)))}
        live = [i for i in items.values() if i.status is not ItemStatus.DELETED]
        ids = [i.id for i in live]
        links = await tx.links(ids)
        linked_ids = {lk.src_item_id for lk in links} | {lk.dst_item_id for lk in links}
        related = {i.id: i for i in await tx.get_items(sorted(linked_ids - set(items)))}
        related.update(items)
        rows = await tx.item_entities(ids)
        entity_ids = {r.entity_id for r in rows} | {
            i.subject_entity_id for i in live if i.subject_entity_id
        }
        entities = {e.id: e for e in await tx.list_entities() if e.id in entity_ids}
        categories = {c.id: c.slug for c in await tx.categories()}
        out: dict[uuid.UUID, list[tuple[KeyKind, str]]] = {}
        for item in live:
            render_entities = [
                RenderEntity(
                    name=entities[r.entity_id].name,
                    role=r.role,
                    kind=entities[r.entity_id].kind.value,
                    label=entities[r.entity_id].labels[0] if entities[r.entity_id].labels else None,
                    type_label=_type_label(entities[r.entity_id].attributes),
                )
                for r in rows
                if r.item_id == item.id and r.entity_id in entities
            ]
            if item.subject_entity_id in entities and not any(
                e.role is EntityRole.ABOUT for e in render_entities
            ):
                subject = entities[item.subject_entity_id]
                render_entities.append(
                    RenderEntity(
                        name=subject.name,
                        role=EntityRole.ABOUT,
                        kind=subject.kind.value,
                        label=subject.labels[0] if subject.labels else None,
                    )
                )
            inp = RenderInput(
                item=item.content,
                timezone=self._tz,
                entities=render_entities,
                category=categories.get(item.category_id) if item.category_id else None,
                superseded_by=_linked(links, related, item.id, LinkType.SUPERSEDES, incoming=True),
                replaced=_linked(links, related, item.id, LinkType.SUPERSEDES, incoming=False),
                fulfilled_by=_linked(links, related, item.id, LinkType.FULFILS, incoming=True),
                corrected_by=_linked(links, related, item.id, LinkType.CORRECTS, incoming=True),
            )
            keys: list[tuple[KeyKind, str]] = [(KeyKind.TEXT, " ".join(item.text.split()))]
            if self._verbal:
                keys.append((KeyKind.VERBAL, render_verbal(inp)))
            change = render_change(inp)
            if change:
                keys.append((KeyKind.CHANGE, change))
            for kind, text in extra.get(item.id, ()):
                clean = " ".join(text.split())
                if clean and not has_relative_time(clean):
                    keys.append((kind, clean))
            out[item.id] = list(dict.fromkeys(keys))
        return out


def _type_label(attributes: Mapping[str, object]) -> str | None:
    value = attributes.get("type")
    return value if isinstance(value, str) and value else None


def _linked(
    links: Sequence[LinkRecord],
    items: Mapping[uuid.UUID, ItemRecord],
    item_id: uuid.UUID,
    link_type: LinkType,
    *,
    incoming: bool,
) -> ItemContent | None:
    """The item on the other end of a ``link_type`` link (``incoming``: it points at us)."""
    for link in links:
        if link.link_type is not link_type:
            continue
        if incoming and link.dst_item_id == item_id and link.src_item_id in items:
            return items[link.src_item_id].content
        if not incoming and link.src_item_id == item_id and link.dst_item_id in items:
            return items[link.dst_item_id].content
    return None

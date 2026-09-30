"""Ledger 47 (S4.7): search keys saved while the embedding provider was down get their vectors
later, and are found by words until then. Nothing is asked of the provider when nothing waits."""

from collections.abc import Sequence

from secondmind.core import KeyKind
from secondmind.memory import KeyRecord
from tests.unit.memory.helpers import NOW, create, item, memory, scope, turn


class Provider:
    """An embedder that can be down, and counts what it is asked for."""

    def __init__(self) -> None:
        self.down = True
        self.asked: list[list[str]] = []

    async def __call__(self, texts: Sequence[str], hits: int) -> list[list[float]] | None:
        self.asked.append(list(texts))
        if self.down:
            return None
        return [[float(len(t)), 1.0, 0.0] for t in texts]


async def test_keys_stored_without_vectors_get_them_when_the_provider_answers() -> None:
    mem, _ = memory()
    ws = scope()
    provider = Provider()
    op = create(item("I prefer oolong tea in the morning"))
    writer = mem.writer(ws, turn(ws, now=NOW))
    writer.add(op)
    await writer.commit()
    indexer = mem.keys(ws, timezone="UTC", embed=provider, model="fake:e@3")
    await indexer.rebuild([op.item_id])

    stored = await mem.reader(ws).keys([op.item_id])
    assert stored
    assert all(k.embedding is None for k in stored)  # kept, found by words

    # Still down: nothing changes, and the keys are not lost.
    assert await indexer.embed_missing() == 0
    assert all(k.embedding is None for k in await mem.reader(ws).keys([op.item_id]))

    provider.down = False
    asked_before = len(provider.asked)
    assert await indexer.embed_missing() == len(stored)
    embedded = await mem.reader(ws).keys([op.item_id])
    assert all(k.embedding is not None and k.embedding_model == "fake:e@3" for k in embedded)
    assert len(provider.asked) == asked_before + 1

    # Nothing left: the provider is not called at all.
    assert await indexer.embed_missing() == 0
    assert len(provider.asked) == asked_before + 1


async def test_a_pages_passages_are_left_to_the_link_workflow() -> None:
    mem, db = memory()
    ws = scope()
    op = create(item("for the sleep tips"))
    writer = mem.writer(ws, turn(ws, now=NOW))
    writer.add(op)
    await writer.commit()
    indexer = mem.keys(ws, timezone="UTC", embed=None, model="none")
    await indexer.rebuild([op.item_id])
    chunk = KeyRecord(
        id=op.item_id,  # any id will do
        workspace_id=ws.workspace_id,
        item_id=op.item_id,
        key_kind=KeyKind.CHUNK,
        text="a passage of the page",
        content_hash="x",
        position=0,
    )
    async with db.store(ws).transaction() as tx:
        await tx.replace_keys(op.item_id, (), [chunk])
    waiting = await indexer.pending()
    assert waiting
    assert all(k.key_kind is not KeyKind.CHUNK for k in waiting)

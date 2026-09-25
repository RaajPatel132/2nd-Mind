"""S3.9: what was said is indexed after a turn: the message whole, the reply by paragraph and
list item, embeddings reused by hash, and only completed chat turns."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from secondmind.memory import content_hash
from secondmind.retrieval import ConversationIndexer, SaidRow, SaidTurn, split_said

AT = datetime(2026, 9, 29, 13, 30, tzinfo=UTC)
REPLY = """Here are three light reads for a slow weekend:

1. Tea by the Window by Arun Mehra: gentle short stories.
2. The Map of Small Things by Clara Voss: a warm
   travel memoir.
3. Seven Easy Mornings by Tomas Reed: calm routines.

Enjoy the weekend."""


class MemoryStore:
    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, list[SaidRow]] = {}
        self.cache: dict[str, list[float]] = {}

    async def cached_embeddings(self, hashes: Sequence[str], model: str) -> dict[str, list[float]]:
        return {h: v for h, v in self.cache.items() if h in hashes}

    async def replace_turn(self, turn_id: uuid.UUID, rows: Sequence[SaidRow]) -> None:
        self.rows[turn_id] = list(rows)

    async def indexed_turns(self, turn_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
        return {t for t in turn_ids if t in self.rows}


class Embedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(self, texts: Sequence[str], hits: int) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(t)), 1.0] for t in texts]


def said(output: str | None = REPLY, *, completed: bool = True, chat: bool = True) -> SaidTurn:
    return SaidTurn(
        id=uuid.uuid4(),
        input="Can you suggest three books?",
        output=output,
        started_at=AT,
        completed=completed,
        chat=chat,
    )


def test_a_reply_splits_by_paragraph_and_list_item() -> None:
    assert split_said(REPLY) == [
        "Here are three light reads for a slow weekend:",
        "Tea by the Window by Arun Mehra: gentle short stories.",
        "The Map of Small Things by Clara Voss: a warm travel memoir.",
        "Seven Easy Mornings by Tomas Reed: calm routines.",
        "Enjoy the weekend.",
    ]


async def test_a_turn_is_indexed_as_its_message_and_its_reply_snippets() -> None:
    store, embed = MemoryStore(), Embedder()
    turn = said()
    written = await ConversationIndexer(store, embed=embed, model="m").index(turn)
    rows = store.rows[turn.id]
    assert written == 6
    assert [(r.role, r.seq) for r in rows] == [("user", 0)] + [("assistant", n) for n in range(5)]
    assert all(r.said_at == AT and r.embedding is not None for r in rows)


async def test_embeddings_are_reused_by_content_hash() -> None:
    store, embed = MemoryStore(), Embedder()
    store.cache[content_hash("Enjoy the weekend.")] = [9.0, 9.0]
    turn = said()
    await ConversationIndexer(store, embed=embed, model="m").index(turn)
    assert "Enjoy the weekend." not in embed.calls[0]
    assert store.rows[turn.id][-1].embedding == [9.0, 9.0]


async def test_failed_and_non_chat_turns_are_not_indexed() -> None:
    store = MemoryStore()
    indexer = ConversationIndexer(store, embed=Embedder(), model="m")
    assert await indexer.index(said(completed=False)) == 0
    assert await indexer.index(said(chat=False)) == 0
    assert await indexer.index(said(output=None)) == 0
    assert store.rows == {}


async def test_backfill_indexes_only_what_is_missing_and_twice_is_harmless() -> None:
    store = MemoryStore()
    indexer = ConversationIndexer(store, embed=Embedder(), model="m")
    first, second = said(), said()
    await indexer.index(first)
    assert await indexer.backfill([first, second]) == 6
    assert set(store.rows) == {first.id, second.id}
    await indexer.index(first)  # indexing again replaces, it doesn't duplicate
    assert len(store.rows[first.id]) == 6

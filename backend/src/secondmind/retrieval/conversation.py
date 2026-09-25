"""Conversation recall's index (S3.9, FR-6.10): what was *said*, kept apart from what was *saved*.

After a turn completes, its stored user message and its reply are written into
``conversation_keys``: the message as one snippet, the reply split by paragraph and list item,
so "the books you suggested" can land on the list. Only completed chat turns are indexed, and
only their stored text (the input is already redacted if a secret was found). Embeddings are
reused by content hash. Undo never touches this index: undo reverts writes, not the
conversation.
"""

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from secondmind.core import new_id
from secondmind.memory import Embedder, content_hash

Role = Literal["user", "assistant"]
_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_MAX_SNIPPET_CHARS = 1_200


@dataclass(frozen=True, slots=True)
class Snippet:
    role: Role
    seq: int
    text: str


@dataclass(frozen=True, slots=True)
class SaidRow:
    """One ``conversation_keys`` row to write."""

    id: uuid.UUID
    turn_id: uuid.UUID
    role: Role
    seq: int
    said_at: datetime
    text: str
    content_hash: str
    embedding: list[float] | None
    embedding_model: str | None


@dataclass(frozen=True, slots=True)
class SaidTurn:
    """What the indexer needs from a turn (the agent's ``Turn`` has all of it)."""

    id: uuid.UUID
    input: str
    output: str | None
    started_at: datetime
    completed: bool
    chat: bool


class ConversationStore(Protocol):
    async def cached_embeddings(
        self, hashes: Sequence[str], model: str
    ) -> dict[str, list[float]]: ...

    async def replace_turn(self, turn_id: uuid.UUID, rows: Sequence[SaidRow]) -> None:
        """Delete the turn's rows and write ``rows`` (indexing a turn twice is harmless)."""
        ...

    async def indexed_turns(self, turn_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]: ...


def split_said(text: str) -> list[str]:
    """A reply as snippets: one per paragraph, one per list item. Headings like "Here are
    three:" stay with nothing else; very long paragraphs are kept whole but capped."""
    snippets: list[str] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if any(_LIST_ITEM.match(ln) for ln in lines):
            current: list[str] = []
            for line in lines:
                if _LIST_ITEM.match(line) and current:
                    snippets.append(" ".join(current))
                    current = []
                current.append(_LIST_ITEM.sub("", line).strip())
            if current:
                snippets.append(" ".join(current))
        elif lines:
            snippets.append(" ".join(ln.strip() for ln in lines))
    return [" ".join(s.split())[:_MAX_SNIPPET_CHARS] for s in snippets if s.strip()]


class ConversationIndexer:
    def __init__(self, store: ConversationStore, *, embed: Embedder | None, model: str) -> None:
        self._store = store
        self._embed = embed
        self._model = model

    async def index(self, turn: SaidTurn) -> int:
        """Index one turn; returns how many snippets were written (0 when it isn't indexed)."""
        if not turn.completed or not turn.chat or not turn.output:
            return 0
        snippets = [Snippet("user", 0, " ".join(turn.input.split())[:_MAX_SNIPPET_CHARS])]
        snippets += [Snippet("assistant", n, s) for n, s in enumerate(split_said(turn.output))]
        snippets = [s for s in snippets if s.text]
        hashes = sorted({content_hash(s.text) for s in snippets})
        vectors = await self._store.cached_embeddings(hashes, self._model)
        misses = sorted({s.text for s in snippets if content_hash(s.text) not in vectors})
        if self._embed is not None and (misses or vectors):
            fresh = await self._embed(misses, len(hashes) - len(misses))
            if fresh is not None:
                vectors.update({content_hash(t): v for t, v in zip(misses, fresh, strict=True)})
        rows = [
            SaidRow(
                id=new_id(),
                turn_id=turn.id,
                role=s.role,
                seq=s.seq,
                said_at=turn.started_at,
                text=s.text,
                content_hash=content_hash(s.text),
                embedding=vectors.get(content_hash(s.text)),
                embedding_model=self._model if content_hash(s.text) in vectors else None,
            )
            for s in snippets
        ]
        await self._store.replace_turn(turn.id, rows)
        return len(rows)

    async def backfill(self, turns: Sequence[SaidTurn]) -> int:
        """Index the turns not indexed yet (the one-off job for turns from before S3)."""
        done = await self._store.indexed_turns([t.id for t in turns])
        total = 0
        for turn in turns:
            if turn.id not in done:
                total += await self.index(turn)
        return total

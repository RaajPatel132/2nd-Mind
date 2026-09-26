"""Triggers on people and topics (S3.10, FR-6.9): checked on **every** turn.

A deterministic **mention scan** (S2's matcher over names, aliases and "my <label>", no model
call) finds the entities a message mentions; pending ``on: person`` triggers for them fire.
``on: topic`` and ``on: situation`` triggers fire when the message's embedding is close enough
to the trigger's cue (``TRIGGER_SIMILARITY_THRESHOLD``); the message is embedded at most once
per turn, and a recall's embedding is reused.

**Firing is a write**: the trigger becomes ``fired`` through ``MemoryWriter`` in the same turn,
so it shows in the diff and undo puts it back to ``pending``. The reply ends with a note built
from a template, with no extra model call. The task behind it stays open until it's done.
"""

import math
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from secondmind.core import AgentStep, Kind, Trail, TriggerOn, TriggerState, WorkspaceScope
from secondmind.ingestion import mentions_in
from secondmind.memory import (
    ItemRecord,
    Memory,
    MemoryReader,
    SetTriggerState,
    TriggerRecord,
    WriterTurn,
    content_hash,
)

# Given texts, their vectors (None when embeddings are unavailable).
EmbedTexts = Callable[[Sequence[str]], Awaitable[list[list[float]] | None]]


@dataclass(frozen=True, slots=True)
class Fired:
    trigger_id: uuid.UUID
    item_id: uuid.UUID
    why: str
    note: str


def note_for(item: ItemRecord) -> str:
    """ "By the way, you wanted to ask Nisha about her interview." (a template, no model)."""
    text = " ".join(item.text.split()).rstrip(".")
    remember = re.match(r"^(remember|remind me( of| about)?)\s+(.*)$", text, re.IGNORECASE)
    if remember:
        return f"By the way, you asked me to remind you: {remember.group(3)}."
    if item.kind is Kind.TASK:
        return f"By the way, you wanted to {text[:1].lower()}{text[1:]}."
    return f"By the way, you asked me to remind you about {item.title}."


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class TriggerCheck:
    def __init__(self, *, threshold: float) -> None:
        self._threshold = threshold

    async def run(
        self,
        *,
        memory: Memory,
        scope: WorkspaceScope,
        turn: WriterTurn,
        message: str,
        vector: Sequence[float] | None,
        embed: EmbedTexts | None,
        trail: Trail,
    ) -> list[Fired]:
        """Check the message and fire what matches; returns what fired (empty: nothing)."""
        reader = memory.reader(scope)
        pending = await reader.pending_triggers(
            [TriggerOn.PERSON, TriggerOn.TOPIC, TriggerOn.SITUATION]
        )
        if not pending:
            return []
        fired: list[tuple[TriggerRecord, str]] = []
        people = [t for t in pending if t.on is TriggerOn.PERSON]
        if people:
            mentioned = {e.id: hit for e, hit in mentions_in(message, await reader.entities())}
            for t in people:
                entity = _uuid(t.spec.get("entity_id"))
                if entity is not None and entity in mentioned:
                    fired.append((t, f"you mentioned {mentioned[entity]!r}"))
        topics = [t for t in pending if t.on in (TriggerOn.TOPIC, TriggerOn.SITUATION)]
        if topics and (vector is not None or embed is not None):
            fired += await self._topics(reader, topics, message, vector, embed)
        if not fired:
            return []
        items = {i.id: i for i in await reader.items([t.item_id for t, _ in fired])}
        out = [
            Fired(trigger_id=t.id, item_id=t.item_id, why=why, note=note_for(items[t.item_id]))
            for t, why in fired
            if t.item_id in items
        ]
        async with trail.run(AgentStep.TRIGGERS):
            writer = memory.writer(scope, turn, emit=trail.emit)
            writer.add(
                *(
                    SetTriggerState(
                        trigger_id=f.trigger_id,
                        state=TriggerState.FIRED,
                        title=f"reminder: {items[f.item_id].title}",
                        rationale=f.why,
                    )
                    for f in out
                )
            )
            await writer.commit()
        return out

    async def _topics(
        self,
        reader: MemoryReader,
        topics: Sequence[TriggerRecord],
        message: str,
        vector: Sequence[float] | None,
        embed: EmbedTexts | None,
    ) -> list[tuple[TriggerRecord, str]]:
        cues = {t.id: str(t.spec.get("cue") or "") for t in topics}
        keys = await reader.keys(sorted({t.item_id for t in topics}))
        by_hash = {k.content_hash: k.embedding for k in keys if k.embedding is not None}
        cue_vectors: dict[uuid.UUID, Sequence[float]] = {}
        missing: list[str] = []
        for t in topics:
            cue = cues[t.id]
            if not cue:
                continue
            found = by_hash.get(str(t.spec.get("cue_hash") or content_hash(cue)))
            if found is not None:
                cue_vectors[t.id] = found
            else:
                missing.append(cue)
        texts = ([] if vector is not None else [message]) + sorted(set(missing))
        fresh: dict[str, Sequence[float]] = {}
        if texts and embed is not None:
            got = await embed(texts)
            fresh = dict(zip(texts, got or [], strict=False))
        query = vector if vector is not None else fresh.get(message)
        if query is None:
            return []
        out: list[tuple[TriggerRecord, str]] = []
        for t in topics:
            cue_vector = cue_vectors.get(t.id) or fresh.get(cues[t.id])
            if cue_vector is None:
                continue
            score = _cosine(query, cue_vector)
            if score >= self._threshold:
                out.append((t, f"the topic matched {cues[t.id]!r} ({score:.2f})"))
        return out


def _uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None

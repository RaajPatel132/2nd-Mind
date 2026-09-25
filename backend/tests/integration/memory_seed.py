"""Real turns and a memory that touches every memory table, for integration tests. Everything is
written through the real writer and key indexer, so seeding also exercises ``SqlMemoryStore``.
All data is synthetic."""

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from secondmind.agent import TurnKind
from secondmind.agent.adapters import SqlTurnStore
from secondmind.auth.adapters import SqlIdentityStore
from secondmind.core import (
    EntityRole,
    Kind,
    LinkType,
    TriggerOn,
    VocabKind,
    WorkspaceScope,
    new_id,
    utc_now,
)
from secondmind.memory import (
    LinkItems,
    Memory,
    NewTrigger,
    RelateEntities,
    TriggerContent,
    UpdateItem,
    WriterTurn,
)
from secondmind.memory.adapters import EMBED_DIMENSIONS, Database, sql_memory
from secondmind.retrieval import ConversationIndexer, SaidTurn
from secondmind.retrieval.adapters import SqlConversationStore
from tests.unit.memory.helpers import create, item, person, sensitive

EMBED_MODEL = f"fake:fake-embed@{EMBED_DIMENSIONS}"


async def workspace(identity: SqlIdentityStore) -> WorkspaceScope:
    user, ws = await identity.ensure_user_with_private_workspace(
        email=f"{new_id()}@example.test", timezone="UTC"
    )
    return WorkspaceScope(workspace_id=ws.id, user_id=user.id)


async def real_turn(
    db: Database,
    scope: WorkspaceScope,
    kind: TurnKind = TurnKind.USER,
    parent: uuid.UUID | None = None,
) -> WriterTurn:
    """A stored turn row (the write log and items reference it) and its writer view."""
    now = utc_now()
    stored = await SqlTurnStore(db, scope).create(
        turn_id=new_id(),
        text="synthetic message",
        config_hash="0" * 64,
        started_at=now,
        kind=kind,
        parent_turn_id=parent,
    )
    return WriterTurn(turn_id=stored.id, workspace_id=scope.workspace_id, kind=kind.value, now=now)


def vector(text: str, dims: int = EMBED_DIMENSIONS) -> list[float]:
    """A deterministic unit-free vector: equal texts give equal vectors."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    out = [0.0] * dims
    for i, byte in enumerate(digest):
        out[(byte * 31 + i * 97) % dims] += 1.0 + i / 100
    return out


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(self, texts: Sequence[str], cache_hits: int) -> list[list[float]]:
        self.calls.append(list(texts))
        return [vector(t) for t in texts]


@dataclass(frozen=True)
class SeededMemory:
    turn: WriterTurn
    person_id: uuid.UUID
    item_id: uuid.UUID
    task_id: uuid.UUID
    held_id: uuid.UUID


async def seed_memory(db: Database, scope: WorkspaceScope) -> SeededMemory:
    """One turn that writes a row into every memory table, then its keys."""
    memory = Memory(sql_memory(db))
    self_id = (await memory.reader(scope).self_entity()).id
    turn = await real_turn(db, scope)
    nisha = person("Nisha", "sister", key=True)
    tulips = create(
        item("Nisha likes tulips", Kind.PREFERENCE), (nisha.entity_id, EntityRole.ABOUT)
    )
    tulips = tulips.model_copy(update={"category_slug": "gifts", "category_name": "Gifts"})
    task = create(
        item("Buy tulips for Nisha", Kind.TASK, due_at=turn.now + timedelta(days=3)),
        (nisha.entity_id, EntityRole.ABOUT),
    )
    task = task.model_copy(
        update={
            "triggers": [
                NewTrigger(
                    trigger_id=new_id(),
                    trigger=TriggerContent(
                        on=TriggerOn.TIME, fires_at=turn.now + timedelta(days=2)
                    ),
                )
            ]
        }
    )
    therapy = create(sensitive(item("I see a therapist on Tuesdays", Kind.FACT)))
    writer = memory.writer(scope, turn)
    writer.add(
        nisha,
        tulips,
        task,
        LinkItems(
            link_id=new_id(),
            src_id=task.item_id,
            link_type=LinkType.BECAUSE,
            dst_id=tulips.item_id,
            title="buying tulips because she likes them",
        ),
        RelateEntities(
            relation_id=new_id(),
            src_entity_id=self_id,
            relation="sibling",
            dst_entity_id=nisha.entity_id,
            title="Nisha is my sister",
        ),
        therapy,
        UpdateItem(item_id=therapy.item_id, changes={"in_core": True}, title="therapy"),
    )
    writer.register_vocab(VocabKind.PREDICATE, "likes", ["enjoys"])
    result = await writer.commit()
    held = [e.held_write_id for e in result.diff.entries if e.held_write_id is not None]
    assert len(held) == 1, result.diff.entries
    keys = memory.keys(scope, timezone="UTC", embed=FakeEmbedder(), model=EMBED_MODEL)
    await keys.rebuild([tulips.item_id, task.item_id, therapy.item_id])
    # What was said (conversation_keys) and what recall retrieved (item_access).
    indexer = ConversationIndexer(
        SqlConversationStore(db, scope), embed=FakeEmbedder(), model=EMBED_MODEL
    )
    await indexer.index(
        SaidTurn(
            id=turn.turn_id,
            input="Nisha likes tulips",
            output="Noted: Nisha likes tulips.\n\n- Buy tulips for Nisha",
            started_at=turn.now,
            completed=True,
            chat=True,
        )
    )
    await memory.record_access(
        scope, turn_id=turn.turn_id, at=turn.now, retrieved=[tulips.item_id], cited=[]
    )
    return SeededMemory(
        turn=turn,
        person_id=nisha.entity_id,
        item_id=tulips.item_id,
        task_id=task.item_id,
        held_id=held[0],
    )

"""The recall fixture (S3.1) in real Postgres: the main workspace plus the second one with
overlapping names, seeded through the real writer, key indexer and conversation index, with the
offline bag-of-words embeddings."""

from collections.abc import Sequence
from dataclasses import dataclass

from secondmind.agent.adapters import SqlTurnStore
from secondmind.auth.adapters import SqlIdentityStore
from secondmind.core import WorkspaceScope, new_id
from secondmind.evals.fixture import RecallFixture, Seeded, load_fixture, seed_workspace
from secondmind.memory import Memory
from secondmind.memory.adapters import EMBED_DIMENSIONS, Database, sql_memory
from secondmind.providers import FakeProvider
from secondmind.retrieval.adapters import SqlConversationStore

EMBED_MODEL = f"fake:fake-embed@{EMBED_DIMENSIONS}"
_FAKE = FakeProvider()


async def fake_embed(texts: Sequence[str], cache_hits: int = 0) -> list[list[float]]:
    if not texts:
        return []
    return (await _FAKE.embed("fake-embed", list(texts), EMBED_DIMENSIONS)).vectors


async def embed_one(text: str) -> list[float]:
    return (await fake_embed([text]))[0]


@dataclass(frozen=True)
class RecallWorkspaces:
    fixture: RecallFixture
    main: Seeded
    other: Seeded

    def ids(self, *keys: str) -> set[object]:
        return {self.main.items[k] for k in keys}


async def fixture_workspace(identity: SqlIdentityStore, timezone: str) -> WorkspaceScope:
    user, ws = await identity.ensure_user_with_private_workspace(
        email=f"recall-{new_id()}@example.test", timezone=timezone
    )
    return WorkspaceScope(workspace_id=ws.id, user_id=user.id)


async def seed_recall(db: Database, identity: SqlIdentityStore) -> RecallWorkspaces:
    fixture = load_fixture()
    memory = Memory(sql_memory(db))
    seeded = []
    for spec in (fixture, fixture.other):
        scope = await fixture_workspace(identity, fixture.timezone)
        seeded.append(
            await seed_workspace(
                spec,
                memory=memory,
                turns=SqlTurnStore(db, scope),
                scope=scope,
                timezone=fixture.timezone,
                now=fixture.instant,
                embed=fake_embed,
                embedding_model=EMBED_MODEL,
                conversation=SqlConversationStore(db, scope),
            )
        )
    return RecallWorkspaces(fixture=fixture, main=seeded[0], other=seeded[1])

"""The persona template in real Postgres: the seed loaded through the real writer, key indexer,
conversation index and link store, with the offline embeddings (S4.10, S4.11)."""

from dataclasses import dataclass
from datetime import UTC, datetime

from secondmind.agent.adapters import SqlTurnStore
from secondmind.auth import Workspace
from secondmind.auth.adapters import SqlIdentityStore
from secondmind.links.adapters import SqlLinkStore
from secondmind.memory import Memory
from secondmind.memory.adapters import Database, sql_memory
from secondmind.persona import (
    PersonaSeed,
    PersonaService,
    Seeded,
    TemplateDeps,
    load_persona,
    seed_template,
)
from secondmind.persona.adapters import SqlPersonaStore
from secondmind.retrieval.adapters import SqlConversationStore
from tests.integration.recall_seed import EMBED_MODEL, fake_embed


@dataclass
class PersonaWorld:
    seed: PersonaSeed
    template: Workspace
    seeded: Seeded
    identity: SqlIdentityStore
    db: Database
    embed_calls: list[list[str]]

    def service(self, *, today: datetime | None = None) -> PersonaService:
        clock_now = today or datetime(2026, 10, 5, 4, 30, tzinfo=UTC)
        return PersonaService(
            identity=self.identity,
            store=SqlPersonaStore(self.db),
            memory=Memory(sql_memory(self.db)),
            seed_id=self.seed.id,
            timezone=self.seed.timezone,
            anchor=self.seed.anchor,
            clock=lambda: clock_now,
        )


async def load_template(db: Database, identity: SqlIdentityStore) -> PersonaWorld:
    """Load the Aditi Rao seed into a template workspace (replacing any earlier one)."""
    seed = load_persona()
    calls: list[list[str]] = []

    async def counting_embed(texts, hits=0):  # type: ignore[no-untyped-def]
        calls.append(list(texts))
        return await fake_embed(texts, hits)

    deps = TemplateDeps(
        identity=identity,
        store=SqlPersonaStore(db),
        memory=Memory(sql_memory(db)),
        turns=lambda scope: SqlTurnStore(db, scope),
        conversation=lambda scope: SqlConversationStore(db, scope),
        sources=lambda scope: SqlLinkStore(db, scope),
        embed=counting_embed,
        embedding_model=EMBED_MODEL,
    )
    template, seeded = await seed_template(deps, seed, force=True)
    assert seeded is not None
    return PersonaWorld(seed, template, seeded, identity, db, calls)

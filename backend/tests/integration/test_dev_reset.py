"""S3.1: ``make seed-dev`` resets the dev workspace before seeding it, and touches no other."""

import pytest

from secondmind.agent.adapters import SqlTurnStore
from secondmind.auth.adapters import SqlIdentityStore
from secondmind.core import EntityKind
from secondmind.evals.recall import fake_router, load_cases, seed_into
from secondmind.memory import Memory
from secondmind.memory.adapters import Database, reset_workspace, sql_memory
from secondmind.retrieval import Access, Filters
from secondmind.retrieval.adapters import SqlRecallStore
from tests.integration.conftest import PgUrls
from tests.integration.recall_seed import seed_recall

pytestmark = pytest.mark.integration


async def test_reset_empties_one_workspace_and_seeding_again_fills_it(
    pg_urls: PgUrls, app_db: Database, identity: SqlIdentityStore
) -> None:
    seeded = await seed_recall(app_db, identity)
    main, other = seeded.main.scope, seeded.other.scope

    deleted = await reset_workspace(pg_urls.owner, main)
    assert deleted["turns"] >= 1
    assert deleted["entities"] >= 1

    everything = Filters()
    assert (await SqlRecallStore(app_db, main).lookup(everything, Access(), limit=5)).total == 0
    assert await SqlTurnStore(app_db, main).recent(limit=5) == []
    entities = await Memory(sql_memory(app_db)).reader(main).entities()
    assert [(e.kind, e.name) for e in entities] == [(EntityKind.SELF, "me")]
    assert (await SqlRecallStore(app_db, other).lookup(everything, Access(), limit=5)).total > 0
    assert await SqlTurnStore(app_db, other).recent(limit=5) != []

    again = await seed_into(app_db, main, fake_router(load_cases()))
    assert again is not None
    assert len(again.items) == len(seeded.main.items)

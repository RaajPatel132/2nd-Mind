"""Ledger 47 on Postgres: a save made while the embedding provider was down keeps its search keys
without vectors; once the provider answers, the periodic job embeds them and puts the cost on the
ledger as the app's own."""

import pytest
from sqlalchemy import text

from secondmind.auth.adapters import SqlIdentityStore
from secondmind.core import TargetType
from secondmind.evals.recall import eval_runner, fake_router, load_cases
from secondmind.memory.adapters import Database
from secondmind.providers import ProviderErrorKind
from tests.integration.memory_seed import workspace

pytestmark = pytest.mark.integration


async def test_keys_saved_without_vectors_are_embedded_later_as_the_apps_cost(
    app_db: Database, identity: SqlIdentityStore
) -> None:
    from datetime import UTC, datetime  # noqa: PLC0415

    scope = await workspace(identity)
    router = fake_router(load_cases())
    provider = router._adapters["fake"]
    provider.embed_error = ProviderErrorKind.CONNECTION
    runner = eval_runner(app_db, router, now=datetime(2026, 9, 30, 10, 0, tzinfo=UTC))
    handle = await runner.start(scope, text="I like oolong tea", timezone="UTC")
    async for _ in handle.events():
        pass
    rows = await runner.memory.reader(scope).write_log(handle.turn.id)
    ids = list(dict.fromkeys(r.target_id for r in rows if r.target_type is TargetType.ITEM))
    assert ids
    keys = await runner.memory.reader(scope).keys(ids)
    assert keys
    assert all(k.embedding is None for k in keys)  # saved anyway, found by words

    # Still down: nothing is lost and nothing changes.
    assert await runner.embed_pending_keys(scope, timezone="UTC") == 0
    assert all(k.embedding is None for k in await runner.memory.reader(scope).keys(ids))

    provider.embed_error = None
    assert await runner.embed_pending_keys(scope, timezone="UTC") == len(keys)
    embedded = await runner.memory.reader(scope).keys(ids)
    assert all(k.embedding is not None and k.embedding_model for k in embedded)
    assert await runner.embed_pending_keys(scope, timezone="UTC") == 0  # nothing left

    # The late calls are the app's cost: they never count against the person's allowance.
    async with app_db.workspace(scope) as session:
        late = (
            await session.execute(
                text(
                    "SELECT count(*) FILTER (WHERE system), count(*) FILTER (WHERE NOT system) "
                    "FROM usage_ledger WHERE turn_id = :t AND step = 'embed'"
                ),
                {"t": handle.turn.id},
            )
        ).one()
    assert late[0] >= 1
    await runner.aclose()

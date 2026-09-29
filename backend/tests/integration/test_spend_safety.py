"""R.10 / ADR-0032 against real Postgres and Redis: migration 0005 (tier, the audit the app can't
write, the totals-only spend function), the ledger's system flag, the shared kill switch, and
counters that agree with the ledger."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from secondmind.api import Services, build_services, create_app
from secondmind.auth.adapters import SqlAdminStore, SqlIdentityStore
from secondmind.config import load_app_config
from secondmind.core import NotFoundError, Tier, WorkspaceScope, new_id
from secondmind.memory.adapters import Database
from secondmind.metering import LedgerEntry, SpendGate, SpendLimits, SpendTotals
from secondmind.metering.adapters import (
    RedisSpendStore,
    SqlLedgerReader,
    SqlSpendReader,
    insert_ledger_entry,
)
from tests.conftest import BASE_ENV
from tests.integration.conftest import BACKEND, PgUrls, run_migrations

pytestmark = pytest.mark.integration


async def _user(identity: SqlIdentityStore, email: str) -> tuple[uuid.UUID, uuid.UUID]:
    user, workspace = await identity.ensure_user_with_private_workspace(email=email, timezone="UTC")
    return user.id, workspace.id


async def _turn_and_ledger(
    owner_db: Database,
    workspace_id: uuid.UUID,
    rows: list[tuple[str, str, str, bool]],
    *,
    kind: str = "user",
) -> uuid.UUID:
    """A turn with one ledger row per ``(step, provider, cost, system)``."""
    turn_id = new_id()
    async with owner_db.identity() as session:
        owner_id = (
            await session.execute(
                text("SELECT owner_user_id FROM workspaces WHERE id = :w"), {"w": workspace_id}
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO turns (id, workspace_id, user_id, input, status, config_hash,"
                " started_at, kind) VALUES (:t, :w, :u, 'x', 'completed', 'h', now(), :k)"
            ),
            {"t": turn_id, "w": workspace_id, "u": owner_id, "k": kind},
        )
        for step, provider, cost, system in rows:
            await session.execute(
                text(
                    "INSERT INTO usage_ledger (id, workspace_id, owner_user_id, turn_id, step,"
                    " provider, model, input_tokens, cached_input_tokens, output_tokens,"
                    " cost_usd, charged_tokens, price_version, system)"
                    " VALUES (:id, :w, :u, :t, :s, :p, 'm', 10, 0, 5, :c, 15, 'v', :sys)"
                ),
                {
                    "id": new_id(),
                    "w": workspace_id,
                    "u": owner_id,
                    "t": turn_id,
                    "s": step,
                    "p": provider,
                    "c": Decimal(cost),
                    "sys": system,
                },
            )
    return turn_id


async def test_a_new_user_is_standard_and_the_admin_store_audits_every_change(
    app_db: Database, owner_db: Database
) -> None:
    identity = SqlIdentityStore(app_db)
    user_id, _ = await _user(identity, f"tier-{new_id().hex[:8]}@example.test")
    assert (await identity.get_user(user_id)).tier is Tier.STANDARD  # type: ignore[union-attr]

    email = (await identity.get_user(user_id)).email  # type: ignore[union-attr]
    assert email is not None
    admin = SqlAdminStore(owner_db)
    up = await admin.set_tier(email=email.upper(), tier=Tier.PREMIUM, changed_by="raj (admin CLI)")
    assert (up.from_tier, up.to_tier, up.changed_by) == (
        Tier.STANDARD,
        Tier.PREMIUM,
        "raj (admin CLI)",
    )
    await admin.set_tier(email=email, tier=Tier.GUEST, changed_by="raj (admin CLI)")
    assert (await identity.get_user(user_id)).tier is Tier.GUEST  # type: ignore[union-attr]
    history = await admin.tier_changes(email)
    assert [(c.from_tier, c.to_tier) for c in history] == [
        (Tier.STANDARD, Tier.PREMIUM),
        (Tier.PREMIUM, Tier.GUEST),
    ]
    with pytest.raises(NotFoundError):
        await admin.set_tier(email="nobody@example.test", tier=Tier.PREMIUM, changed_by="x")


async def test_the_app_role_can_read_the_tier_audit_but_never_write_it(
    app_db: Database, owner_db: Database
) -> None:
    identity = SqlIdentityStore(app_db)
    email = f"audit-{new_id().hex[:8]}@example.test"
    user_id, _ = await _user(identity, email)
    await SqlAdminStore(owner_db).set_tier(email=email, tier=Tier.PREMIUM, changed_by="raj")
    async with app_db.identity() as session:
        seen = (
            await session.execute(
                text("SELECT to_tier FROM tier_changes WHERE user_id = :u"), {"u": user_id}
            )
        ).scalar_one()
    assert seen == "premium"
    for statement in (
        "INSERT INTO tier_changes (user_id, from_tier, to_tier, changed_by)"
        " VALUES (:u, 'standard', 'premium', 'the app')",
        "UPDATE tier_changes SET changed_by = 'the app' WHERE user_id = :u",
        "DELETE FROM tier_changes WHERE user_id = :u",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_db.identity() as session:
                await session.execute(text(statement), {"u": user_id})


async def test_a_tier_outside_the_three_is_refused_by_the_database(owner_db: Database) -> None:
    with pytest.raises(DBAPIError, match="ck_users_tier"):
        async with owner_db.identity() as session:
            await session.execute(
                text("INSERT INTO users (id, email, tier) VALUES (:i, 'x@example.test', 'admin')"),
                {"i": new_id()},
            )


async def test_the_quota_counts_the_persons_rows_and_leaves_system_usage_out(
    app_db: Database, owner_db: Database
) -> None:
    identity = SqlIdentityStore(app_db)
    user_id, workspace_id = await _user(identity, f"ledger-{new_id().hex[:8]}@example.test")
    await _turn_and_ledger(
        owner_db,
        workspace_id,
        [("answer", "anthropic", "0.01000000", False), ("embed", "openai", "0.00050000", True)],
    )
    spent = await SqlLedgerReader(app_db).spent(user_id, [workspace_id])
    assert spent.usd == Decimal("0.01000000")
    assert spent.tokens == 15


async def test_the_sql_ledger_insert_marks_a_system_turn_and_a_background_call_as_system(
    app_db: Database, owner_db: Database
) -> None:
    identity = SqlIdentityStore(app_db)
    user_id, workspace_id = await _user(identity, f"sys-{new_id().hex[:8]}@example.test")
    user_turn = await _turn_and_ledger(owner_db, workspace_id, [])
    system_turn = await _turn_and_ledger(owner_db, workspace_id, [], kind="system")

    def entry(turn: uuid.UUID, *, system: bool) -> LedgerEntry:
        return LedgerEntry(
            workspace_id=workspace_id,
            turn_id=turn,
            step="embed",
            provider="openai",
            model="text-embedding-3-small",
            input_tokens=10,
            cached_input_tokens=0,
            output_tokens=0,
            cost_usd=Decimal("0.00000200"),
            charged_tokens=1,
            price_version="v",
            system=system,
        )

    scope = WorkspaceScope(workspace_id=workspace_id, user_id=user_id)
    async with app_db.workspace(scope) as session:
        await insert_ledger_entry(session, entry(user_turn, system=False))  # the person's
        await insert_ledger_entry(session, entry(user_turn, system=True))  # background indexing
        await insert_ledger_entry(session, entry(system_turn, system=False))  # housekeeping turn
    async with owner_db.identity() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT turn_id = :s, system FROM usage_ledger"
                    " WHERE workspace_id = :w ORDER BY system, 1"
                ),
                {"s": system_turn, "w": workspace_id},
            )
        ).all()
    assert sorted(rows) == [(False, False), (False, True), (True, True)]
    spent = await SqlLedgerReader(app_db).spent(user_id, [workspace_id])
    assert spent.usd == Decimal("0.00000200")  # only the person's own row counts against them


async def test_global_spend_returns_totals_across_workspaces_and_no_rows(
    app_db: Database, owner_db: Database
) -> None:
    identity = SqlIdentityStore(app_db)
    _, ws_a = await _user(identity, f"g-a-{new_id().hex[:6]}@example.test")
    _, ws_b = await _user(identity, f"g-b-{new_id().hex[:6]}@example.test")
    before = await SqlSpendReader(app_db).since(datetime.now(UTC) - timedelta(minutes=1))
    await _turn_and_ledger(owner_db, ws_a, [("answer", "anthropic", "0.02", False)])
    await _turn_and_ledger(owner_db, ws_b, [("embed", "anthropic", "0.01", True)])
    after = await SqlSpendReader(app_db).since(datetime.now(UTC) - timedelta(minutes=1))
    assert after["anthropic"] - before.get("anthropic", Decimal(0)) == Decimal("0.03")  # both
    later = await SqlSpendReader(app_db).since(datetime.now(UTC) + timedelta(hours=1))
    assert later == {}
    # The function is the only way in: the app role still can't read another workspace's rows.
    async with app_db.identity() as session:
        rows = (await session.execute(text("SELECT count(*) FROM usage_ledger"))).scalar_one()
    assert rows == 0


async def test_0005_keeps_users_and_the_ledger_and_goes_down_and_up(pg_urls: PgUrls) -> None:
    owner = make_url(pg_urls.owner)
    admin = create_async_engine(owner, isolation_level="AUTOCOMMIT")
    scratch_name = f"scratch_{new_id().hex[:12]}"
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{scratch_name}"'))
    scratch = owner.set(database=scratch_name).render_as_string(hide_password=False)
    engine = create_async_engine(scratch)
    try:
        await asyncio.to_thread(run_migrations, scratch, "0004")
        u, w, t = new_id(), new_id(), new_id()
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO users (id) VALUES (:u)"), {"u": u})
            await conn.execute(
                text(
                    "INSERT INTO workspaces (id, owner_user_id, kind, timezone)"
                    " VALUES (:w, :u, 'private', 'UTC')"
                ),
                {"w": w, "u": u},
            )
            await conn.execute(
                text(
                    "INSERT INTO turns (id, workspace_id, user_id, input, status, config_hash,"
                    " started_at) VALUES (:t, :w, :u, 'x', 'completed', 'h', now())"
                ),
                {"t": t, "w": w, "u": u},
            )
            await conn.execute(
                text(
                    "INSERT INTO usage_ledger (id, workspace_id, owner_user_id, turn_id, step,"
                    " provider, model, input_tokens, cached_input_tokens, output_tokens,"
                    " cost_usd, charged_tokens, price_version)"
                    " VALUES (:i, :w, :u, :t, 'answer', 'fake', 'm', 1, 0, 1, 0.001, 2, 'v')"
                ),
                {"i": new_id(), "w": w, "u": u, "t": t},
            )
        await asyncio.to_thread(run_migrations, scratch, "head")
        async with engine.connect() as conn:
            tier = (await conn.execute(text("SELECT tier FROM users"))).scalar_one()
            system = (await conn.execute(text("SELECT system FROM usage_ledger"))).scalar_one()
        assert (tier, system) == ("standard", False)  # what existed keeps its meaning
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.attributes["url"] = scratch
        await asyncio.to_thread(command.downgrade, cfg, "0004")
        async with engine.connect() as conn:
            gone = (
                await conn.execute(
                    text(
                        "SELECT to_regclass('tier_changes') IS NULL,"
                        " NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'global_spend')"
                    )
                )
            ).one()
            ledger = (await conn.execute(text("SELECT count(*) FROM usage_ledger"))).scalar_one()
        assert tuple(gone) == (True, True)
        assert ledger == 1
        await asyncio.to_thread(run_migrations, scratch, "head")
    finally:
        await engine.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch_name}" WITH (FORCE)'))
        await admin.dispose()


# ------------------------------------------------------------------ Redis


@pytest.fixture
async def store(redis_url: str) -> AsyncIterator[RedisSpendStore]:
    store = RedisSpendStore.from_url(redis_url)
    await store._redis.flushdb()
    yield store
    await store.aclose()


async def test_the_kill_switch_flag_is_shared_between_processes(redis_url: str) -> None:
    """`make kill-switch on` from one process is seen by another within the 2 s the flag is
    cached, with no restart; the runtime flag wins over the environment's KILL_SWITCH."""
    a = RedisSpendStore.from_url(redis_url)
    b = RedisSpendStore.from_url(redis_url)
    try:
        limits = SpendLimits(daily_usd=Decimal(1), monthly_usd=Decimal(5), kill_switch=False)
        ticker = [100.0]
        gate_b = SpendGate(b, limits, monotonic=lambda: ticker[0])
        assert await gate_b.app_block() is None
        await a.set_kill_switch(True)
        ticker[0] += 2.5
        block = await gate_b.app_block()
        assert block is not None
        assert block.reason.value == "kill_switch"
        await a.set_kill_switch(False)
        ticker[0] += 2.5
        assert await gate_b.app_block() is None
    finally:
        await a.aclose()
        await b.aclose()


async def test_the_redis_counters_add_reset_and_expire_by_period(store: RedisSpendStore) -> None:
    at = datetime(2026, 9, 29, 23, 59, tzinfo=UTC)
    await store.add("anthropic", Decimal("0.10"), at)
    totals = await store.add("openai", Decimal("0.05"), at)
    assert (totals.day, totals.month) == (Decimal("0.15"), Decimal("0.15"))
    assert dict(totals.providers) == {"anthropic": Decimal("0.1"), "openai": Decimal("0.05")}
    next_day = await store.totals(at + timedelta(minutes=2))  # past midnight UTC
    assert next_day.day == 0
    assert next_day.month == Decimal("0.15")
    await store.reset(SpendTotals(day=Decimal("0.02"), month=Decimal("0.02")), at)
    assert (await store.totals(at)).day == Decimal("0.02")
    assert dict((await store.totals(at)).providers) == {}


async def test_a_redis_token_bucket_admits_the_rate_and_says_how_long_to_wait(
    store: RedisSpendStore,
) -> None:
    waits = [await store.take("turns:ada", 3) for _ in range(4)]
    assert waits[:3] == [None, None, None]
    assert waits[3] is not None
    assert 15 < waits[3] <= 20
    assert await store.take("turns:grace", 3) is None


async def test_credit_out_marks_expire_and_a_warning_key_is_set_once(
    store: RedisSpendStore,
) -> None:
    await store.mark_credit_out("openai", 1)
    assert await store.credit_out("openai") is True
    await asyncio.sleep(1.2)
    assert await store.credit_out("openai") is False
    assert await store.once("warn:daily:2026-09-29", 60) is True
    assert await store.once("warn:daily:2026-09-29", 60) is False


# ------------------------------------------------------------------ the counters and the ledger


@pytest.fixture
async def stack(
    pg_urls: PgUrls, redis_url: str
) -> AsyncIterator[tuple[httpx.AsyncClient, Services]]:
    env = BASE_ENV | {
        "DATABASE_URL": pg_urls.app,
        "REDIS_URL": redis_url,
        "MODEL_PROVIDER_MODE": "fake",
        "FAKE_PROVIDER_TOKEN_DELAY_MS": "0",
        "DEV_AUTH": "true",
        "DEV_USER_EMAIL": f"counters-{new_id().hex[:8]}@example.test",
    }
    services = await build_services(load_app_config(env))
    app = create_app(services=services)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            yield c, services
    finally:
        await services.aclose()


async def test_the_spend_counters_agree_with_the_ledger(
    stack: tuple[httpx.AsyncClient, Services], owner_db: Database, pg_urls: PgUrls, redis_url: str
) -> None:
    """Every priced call is counted as it returns and written to the ledger with its turn, so a
    reconcile from the ledger changes nothing that matters. (The fake provider prices its calls
    like the real ones, so this is the same arithmetic.)"""
    client, services = stack
    ws = (await client.post("/v1/auth/dev-login")).json()["workspaces"][0]["id"]
    async with owner_db.identity() as session:
        already = (
            await session.execute(text("SELECT COALESCE(SUM(cost_usd), 0) FROM usage_ledger"))
        ).scalar_one()
    before = await services.gate.store.totals(datetime.now(UTC))
    for message in ("hello", "I live in Bengaluru", "Where do I live?"):
        response = await client.post(f"/v1/workspaces/{ws}/turns", json={"message": message})
        assert "turn.completed" in response.text
    await asyncio.sleep(0.3)  # the worker isn't running here; nothing else writes

    async with owner_db.identity() as session:
        mine = (
            await session.execute(
                text("SELECT COALESCE(SUM(cost_usd), 0) FROM usage_ledger WHERE workspace_id = :w"),
                {"w": ws},
            )
        ).scalar_one()
    assert Decimal(mine) > 0
    after = await services.gate.store.totals(datetime.now(UTC))
    assert after.day - before.day == Decimal(mine)  # counted as each call returned

    db = Database(pg_urls.app, pool_size=1)
    try:
        totals = await services.gate.reconcile(SqlSpendReader(db))
    finally:
        await db.dispose()
    assert totals.day >= Decimal(mine)  # the ledger holds everyone's rows; this test's are in it
    assert totals.day - Decimal(already) >= Decimal(mine) - Decimal("0.000001")

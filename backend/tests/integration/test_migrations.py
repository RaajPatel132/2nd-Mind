"""S1.6 / S2.1: migrations apply cleanly and match the ORM models; the app role cannot bypass
RLS; every workspace gets its self entity."""

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from secondmind.core import new_id
from secondmind.memory.adapters import SCHEMA_HEAD, Database
from tests.integration.conftest import PgUrls, run_migrations

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]


async def test_schema_is_at_head(app_db: Database) -> None:
    assert await app_db.schema_revision() == SCHEMA_HEAD == "0003"


async def test_models_match_migrations(pg_urls: PgUrls) -> None:
    """``alembic check``: autogenerate finds nothing to add (models and migrations agree)."""
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["url"] = pg_urls.owner
    await asyncio.to_thread(command.check, cfg)


async def test_app_role_is_not_owner_superuser_or_rls_bypass(app_db: Database) -> None:
    check = await app_db.role_check()
    assert check.role == "secondmind_app"
    assert check.safe, check


async def test_every_workspace_owned_table_has_rls_and_a_policy(owner_db: Database) -> None:
    """Guard for future tables: a `workspace_id` column means RLS must be on, with a policy."""
    async with owner_db.engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT c.relname, c.relrowsecurity,
                           (SELECT count(*) FROM pg_policies p
                             WHERE p.schemaname = 'public' AND p.tablename = c.relname)
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
                    JOIN information_schema.columns col
                      ON col.table_schema = 'public' AND col.table_name = c.relname
                     AND col.column_name = 'workspace_id'
                    WHERE c.relkind = 'r'
                    ORDER BY c.relname
                    """
                )
            )
        ).all()
    tables = {r[0]: (r[1], r[2]) for r in rows}
    assert set(tables) == {
        "turns",
        "turn_events",
        "usage_ledger",
        "entities",
        "categories",
        "vocab_terms",
        "memory_items",
        "memory_entities",
        "memory_links",
        "entity_relations",
        "memory_keys",
        "triggers",
        "item_versions",
        "write_log",
        "held_writes",
    }
    for name, (rls_on, policies) in tables.items():
        assert rls_on, f"{name} has workspace_id but RLS is off"
        assert policies >= 1, f"{name} has no RLS policy"


async def test_every_new_workspace_gets_exactly_one_self_entity(owner_db: Database) -> None:
    async with owner_db.identity() as session:
        missing = (
            await session.execute(
                text(
                    "SELECT count(*) FROM workspaces w WHERE (SELECT count(*) FROM entities e "
                    "WHERE e.workspace_id = w.id AND e.kind = 'self') <> 1"
                )
            )
        ).scalar_one()
    assert missing == 0


async def test_0002_backfills_self_entities_and_downgrades_cleanly(pg_urls: PgUrls) -> None:
    """A workspace created before 0002 gets its self entity from the backfill; 0002 goes down
    and up again. Runs in a scratch database so the shared one is untouched."""
    owner = make_url(pg_urls.owner)
    admin = create_async_engine(owner, isolation_level="AUTOCOMMIT")
    scratch_name = f"scratch_{new_id().hex[:12]}"
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{scratch_name}"'))
    scratch = owner.set(database=scratch_name).render_as_string(hide_password=False)
    engine = create_async_engine(scratch)
    try:
        await asyncio.to_thread(run_migrations, scratch, "0001")
        user_id, ws_id = new_id(), new_id()
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO users (id) VALUES (:u)"), {"u": user_id})
            await conn.execute(
                text(
                    "INSERT INTO workspaces (id, owner_user_id, kind, timezone) "
                    "VALUES (:w, :u, 'private', 'UTC')"
                ),
                {"w": ws_id, "u": user_id},
            )

        await asyncio.to_thread(run_migrations, scratch, "head")
        async with engine.connect() as conn:
            selves = (
                (
                    await conn.execute(
                        text("SELECT name FROM entities WHERE workspace_id = :w AND kind = 'self'"),
                        {"w": ws_id},
                    )
                )
                .scalars()
                .all()
            )
        assert selves == ["me"]

        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.attributes["url"] = scratch
        await asyncio.to_thread(command.downgrade, cfg, "0001")
        await asyncio.to_thread(run_migrations, scratch, "head")
    finally:
        await engine.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch_name}" WITH (FORCE)'))
        await admin.dispose()


async def test_0003_backfills_charged_tokens_at_weight_one(pg_urls: PgUrls) -> None:
    """Ledger rows and turns from before weighted quota are charged their raw tokens, so the
    quota already spent doesn't move; 0003 goes down and up again."""
    owner = make_url(pg_urls.owner)
    admin = create_async_engine(owner, isolation_level="AUTOCOMMIT")
    scratch_name = f"scratch_{new_id().hex[:12]}"
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{scratch_name}"'))
    scratch = owner.set(database=scratch_name).render_as_string(hide_password=False)
    engine = create_async_engine(scratch)
    try:
        await asyncio.to_thread(run_migrations, scratch, "0002")
        user_id, ws_id, turn_id = new_id(), new_id(), new_id()
        ids = {"u": user_id, "w": ws_id, "t": turn_id}
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO users (id) VALUES (:u)"), ids)
            await conn.execute(
                text(
                    "INSERT INTO workspaces (id, owner_user_id, kind, timezone) "
                    "VALUES (:w, :u, 'private', 'UTC')"
                ),
                ids,
            )
            await conn.execute(
                text(
                    "INSERT INTO turns (id, workspace_id, user_id, input, status, config_hash,"
                    " started_at, input_tokens, cached_input_tokens, output_tokens)"
                    " VALUES (:t, :w, :u, 'hi', 'completed', 'h', now(), 100, 20, 30)"
                ),
                ids,
            )
            await conn.execute(
                text(
                    "INSERT INTO usage_ledger (id, workspace_id, owner_user_id, turn_id, step,"
                    " provider, model, input_tokens, cached_input_tokens, output_tokens,"
                    " cost_usd, price_version)"
                    " VALUES (:i, :w, :u, :t, 'answer', 'fake', 'fake-chat', 100, 20, 30, 0, 'v')"
                ),
                ids | {"i": new_id()},
            )

        await asyncio.to_thread(run_migrations, scratch, "head")
        async with engine.connect() as conn:
            ledger = (await conn.execute(text("SELECT charged_tokens FROM usage_ledger"))).scalar()
            turn = (await conn.execute(text("SELECT charged_tokens FROM turns"))).scalar()
        assert ledger == turn == 150

        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.attributes["url"] = scratch
        await asyncio.to_thread(command.downgrade, cfg, "0002")
        await asyncio.to_thread(run_migrations, scratch, "head")
    finally:
        await engine.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch_name}" WITH (FORCE)'))
        await admin.dispose()

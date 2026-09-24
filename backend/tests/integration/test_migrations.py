"""S1.6: migrations apply cleanly and match the ORM models; the app role cannot bypass RLS."""

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from secondmind.memory.adapters import Database
from tests.integration.conftest import PgUrls

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]


async def test_schema_is_at_head(app_db: Database) -> None:
    assert await app_db.schema_revision() == "0001"


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
    assert set(tables) == {"turns", "turn_events", "usage_ledger"}
    for name, (rls_on, policies) in tables.items():
        assert rls_on, f"{name} has workspace_id but RLS is off"
        assert policies >= 1, f"{name} has no RLS policy"

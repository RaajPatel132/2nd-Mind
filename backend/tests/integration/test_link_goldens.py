"""S4.7 goldens: the link cases of ``evals/cases/links`` on the fake provider, on Postgres, with the
fixture transport. No case touches the internet."""

import pytest

from secondmind.auth.adapters import SqlIdentityStore
from secondmind.evals.links import FixtureTransport, LinkCase, load_cases, run_case
from secondmind.memory.adapters import Database
from tests.integration.memory_seed import workspace

pytestmark = pytest.mark.integration

INGEST = load_cases("ingest")
RECALL = load_cases("recall")


def test_the_suite_has_the_cases_the_sprint_names() -> None:
    assert len(INGEST) == 13
    assert len(RECALL) == 5


@pytest.mark.parametrize("case", INGEST + RECALL, ids=lambda c: c.id)
async def test_each_link_case_passes(
    app_db: Database, identity: SqlIdentityStore, case: LinkCase
) -> None:
    scope = await workspace(identity)
    transport = FixtureTransport()
    result = await run_case(app_db, scope, case, transport=transport)
    assert result.passed, result.failures

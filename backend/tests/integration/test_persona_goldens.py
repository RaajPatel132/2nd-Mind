"""S4.10 / S4.11: the ten persona goldens pass on the fake provider, on a copy moved by 0, 60 and
400 days. A case that passes only on the day the seed was written is a case that would break on
the day a visitor opens it."""

import pytest

from secondmind.auth.adapters import SqlIdentityStore
from secondmind.evals.persona import persona_cases, run_persona_case
from secondmind.evals.recall import RecallCase
from secondmind.memory.adapters import Database
from tests.integration.persona_seed import PersonaWorld, load_template

pytestmark = pytest.mark.integration

CASES = persona_cases()


def test_there_are_ten_cases_including_the_two_suggested_prompts() -> None:
    assert len(CASES) == 10
    ids = {c.id for c in CASES}
    assert {"P01-suggested-save", "P02-suggested-recall"} <= ids


@pytest.fixture(scope="module")
async def world(app_db: Database, identity: SqlIdentityStore) -> PersonaWorld:
    return await load_template(app_db, identity)


@pytest.mark.parametrize("shift", [0, 60, 400])
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
async def test_each_persona_golden_passes_on_a_moved_copy(
    world: PersonaWorld, identity: SqlIdentityStore, case: RecallCase, shift: int
) -> None:
    result = await run_persona_case(
        case,
        db=world.db,
        identity=identity,
        service=world.service(),
        template_items=world.seeded.items,
        shift=shift,
    )
    assert result.passed, result.failures

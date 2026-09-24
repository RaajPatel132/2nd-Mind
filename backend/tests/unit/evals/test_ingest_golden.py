"""S2.13: the ingestion golden cases run as ordinary tests on the fake provider, checking the
pipeline logic (resolver, normalisation, entity resolution, reconciliation, writer, policy,
diff, keys) on fixed model outputs."""

import pytest

from secondmind.evals.ingest import load_cases, run_case, score

CASES = load_cases()


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_golden_case(case: object) -> None:
    run = await run_case(case)  # type: ignore[arg-type]
    result = score(run)
    assert run.turn.status == "completed", run.turn.error_message
    assert not result.failures, "\n".join(result.failures) + f"\nreply: {run.turn.output}"


def test_there_are_at_least_twenty_cases() -> None:
    assert len(CASES) >= 20

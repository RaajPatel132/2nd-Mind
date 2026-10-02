"""Where a recall turn's time goes (ledger 52): only fresh calls count, the reading says how many
were left out, and the performance document quotes a stored run, not numbers typed by hand."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from secondmind.config import DEFAULT_RESOURCES_DIR
from secondmind.evals.profile import (
    END,
    START,
    chain_p50_ms,
    render_profile,
    render_rerank,
    rerank_anatomy,
    step_timings,
    sync_doc,
)
from secondmind.evals.runs import CaseRecord, RunRecord, load_run

DOC = DEFAULT_RESOURCES_DIR.parent / "docs" / "performance.md"
RUN = "20260929T160542Z-1b15a10"


def call(step: str, ms: int | None, **fields: Any) -> dict[str, Any]:
    return {"step": step, "latency_ms": ms, "output_tokens": 100, "cost_usd": 0.0, **fields}


def case(*calls: dict[str, Any], total: int = 1000) -> CaseRecord:
    return CaseRecord(id="c", passed=True, latency_ms=total, calls=list(calls))


def run(*cases: CaseRecord) -> RunRecord:
    return RunRecord(
        run_id="r1",
        suite="recall",
        mode="live",
        routing="x",
        started_at=datetime(2026, 10, 1, tzinfo=UTC),
        git_sha="abc",
        git_dirty=False,
        config_hash="h",
        price_version="p",
        prompt_versions={},
        models={},
        fallbacks={},
        cases=list(cases),
    )


def rows_of(r: RunRecord) -> dict[str, Any]:
    return {t.step: t for t in step_timings(r)}


def test_a_cached_an_estimated_and_a_failed_call_are_left_out_and_counted() -> None:
    r = run(
        case(call("plan", 3000), call("rerank", 2000)),
        case(call("plan", 0, cache_hit=True), call("rerank", 5000, estimated=True)),
        case(call("plan", 1, failed="server"), call("rerank", None)),
    )
    rows = rows_of(r)
    assert (rows["plan"].fresh, rows["plan"].left_out, rows["plan"].p50_ms) == (1, 2, 3000)
    assert (rows["rerank"].fresh, rows["rerank"].left_out) == (1, 3 - 1)


def test_a_call_the_harness_did_not_time_is_not_a_timing() -> None:
    r = run(case(call("answer", 900, timed=False), call("answer", 1100)))
    assert rows_of(r)["answer"].fresh == 1
    assert rows_of(r)["answer"].p50_ms == 1100


def test_the_chain_is_the_sum_of_the_medians_of_the_recall_steps_in_their_order() -> None:
    r = run(
        case(call("intent", 1000), call("plan", 2000), call("rerank", 3000), call("answer", 500)),
        case(call("intent", 1200), call("plan", 4000), call("rerank", 3100), call("answer", 700)),
        case(call("intent", 1400), call("plan", 6000), call("rerank", 3200), call("answer", 900)),
        case(call("extract", 9000)),
    )
    assert [t.step for t in step_timings(r)] == ["intent", "plan", "rerank", "answer", "extract"]
    assert chain_p50_ms(step_timings(r)) == 1200 + 4000 + 3100 + 700  # extract is not on the chain


def test_the_reading_names_the_cases_that_were_timed_end_to_end() -> None:
    mixed = run(
        case(call("plan", 2000), total=5000),
        case(call("plan", 0, cache_hit=True), total=100),
    )
    text = render_profile(mixed)
    assert "2 cases, 1 of them timed end to end" in text
    assert "median from question to end of answer was 5.0 s over 1 cases" in text
    replayed = render_profile(run(case(call("plan", 0, cache_hit=True))))
    assert "No case in this run was timed end to end." in replayed


def scores(*values: float) -> dict[str, Any]:
    return {"scores": [{"id": f"c{n}", "score": v, "reason": "x"} for n, v in enumerate(values, 1)]}


def test_the_rerank_anatomy_counts_single_candidates_depth_and_the_time_fit() -> None:
    r = run(
        case(call("rerank", 1500, output=scores(0.9), output_tokens=100)),
        case(call("rerank", 2000, output=scores(0.9, 0.1, 0.1), output_tokens=200)),
        case(call("rerank", 2500, output=scores(*([0.1] * 11 + [0.8])), output_tokens=300)),
        case(call("rerank", 3000, output=scores(0.2), output_tokens=400)),
    )
    a = rerank_anatomy(r)
    assert a is not None
    assert (a.calls, a.one_candidate, a.one_candidate_kept) == (4, 2, 1)
    assert a.beyond == {5: 1, 10: 1, 15: 0}  # the third call keeps its 12th candidate
    assert a.ms_per_output_token == pytest.approx(5.0)  # a line through (100,1500)…(400,3000)
    assert a.fixed_ms == pytest.approx(1000.0)
    text = render_rerank(r)
    assert "skipping those calls would have changed the outcome in 1 of 4" in text


def test_a_run_with_no_rerank_scores_says_so() -> None:
    assert rerank_anatomy(run(case(call("plan", 100)))) is None
    assert "no rerank scores" in render_rerank(run(case(call("plan", 100))))


def test_sync_doc_replaces_only_the_block_and_is_idempotent() -> None:
    r = run(case(call("plan", 2000), total=2000))
    doc = f"before\n{START}\nold\n{END}\nafter\n"
    once = sync_doc(doc, r)
    assert once.startswith("before\n")
    assert once.endswith(f"{END}\nafter\n")
    assert "old" not in once
    assert sync_doc(once, r) == once
    with pytest.raises(ValueError, match="markers"):
        sync_doc("no markers here", r)


def test_the_performance_document_quotes_the_stored_run_exactly() -> None:
    """Edit the run or the profile code and this fails until the document is regenerated, so a
    number on the page is always one the run file produces."""
    doc = DOC.read_text(encoding="utf-8")
    expected = sync_doc(doc, load_run(RUN))
    assert doc == expected, (
        f"docs/performance.md is out of date: run `python -m secondmind.evals latency {RUN} "
        "--into docs/performance.md`"
    )


def test_the_document_can_be_checked_from_the_command_line(tmp_path: Path) -> None:
    from secondmind.evals.__main__ import main  # noqa: PLC0415

    assert main(["latency", RUN, "--into", str(DOC), "--check"]) == 0
    stale = tmp_path / "stale.md"
    stale.write_text(DOC.read_text(encoding="utf-8").replace("p50", "p51", 1))
    assert main(["latency", RUN, "--into", str(stale), "--check"]) == 1

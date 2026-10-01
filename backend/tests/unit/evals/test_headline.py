"""S4.13, FR-15.3: the landing page's three numbers come from the committed live runs, big enough
to mean something, and say "no run yet" when there is none."""

from datetime import UTC, datetime
from pathlib import Path

from secondmind.evals import headline
from secondmind.evals.runs import CaseRecord, RunRecord, write_run


def run(
    suite: str, stamp: str, cases: list[CaseRecord], *, mode: str = "live", status: str = "complete"
) -> RunRecord:
    return RunRecord(
        run_id=f"{stamp}-abc1234",
        suite=suite,
        mode=mode,  # type: ignore[arg-type]
        routing="configured",
        started_at=datetime(2026, 9, 29, 12, 0, tzinfo=UTC),
        git_sha="abc1234def56",
        git_dirty=False,
        config_hash="0" * 64,
        price_version="2026-09-25",
        prompt_versions={},
        models={"plan": "openai:gpt-6-luna"},
        fallbacks={},
        status=status,  # type: ignore[arg-type]
        cases=cases,
    )


def recall_case(n: int, *, hit: bool, ms: int) -> CaseRecord:
    return CaseRecord(
        id=str(n),
        passed=hit,
        scores={"has_gold": True, "hit5": hit},
        latency_ms=ms,
        calls=[{"step": "plan", "timed": True}],
    )


def ingest_case(n: int, *, date_ok: int, date_of: int) -> CaseRecord:
    return CaseRecord(
        id=str(n), passed=True, scores={"passed": {"date": date_ok}, "total": {"date": date_of}}
    )


def test_without_a_run_every_number_says_so(tmp_path: Path) -> None:
    data = headline.headline(tmp_path)
    assert [m["display"] for m in data["metrics"]] == ["no run yet"] * 3
    assert all(m["value"] is None and m["run"] is None for m in data["metrics"])
    assert data["runs"] == []


def test_the_numbers_are_worked_out_from_the_newest_big_enough_live_run(tmp_path: Path) -> None:
    recall = [recall_case(n, hit=n % 4 != 0, ms=1000 * (n + 1)) for n in range(40)]
    write_run(run("recall", "20260929T120000Z", recall), tmp_path)
    ingest = [ingest_case(n, date_ok=1, date_of=1) for n in range(15)]
    ingest += [ingest_case(99, date_ok=0, date_of=1)]
    write_run(run("ingest", "20260929T130000Z", ingest), tmp_path)
    data = headline.headline(tmp_path)
    by_id = {m["id"]: m for m in data["metrics"]}
    assert by_id["retrieval_hit_rate"]["display"] == "75%"  # 30 of 40
    assert by_id["relative_date_accuracy"]["display"] == "94%"  # 15 of 16
    assert by_id["recall_p95_latency_s"]["value"] == 38.0  # the 38th of 40 cases, in seconds
    assert {r["suite"] for r in data["runs"]} == {"recall", "ingest"}


def test_probes_fakes_and_stopped_runs_are_not_measurements(tmp_path: Path) -> None:
    cases = [recall_case(n, hit=True, ms=500) for n in range(45)]
    write_run(run("recall", "20260929T110000Z", cases[:5]), tmp_path)  # a probe: too few cases
    write_run(run("recall", "20260929T120000Z", cases, mode="fake"), tmp_path)
    write_run(run("recall", "20260929T130000Z", cases, status="stopped: budget"), tmp_path)
    data = headline.headline(tmp_path)
    assert {m["id"]: m["display"] for m in data["metrics"]}["retrieval_hit_rate"] == "no run yet"


def test_the_file_on_disk_is_checked_against_the_runs(tmp_path: Path) -> None:
    out = tmp_path / "headline.json"
    assert not headline.check(out, tmp_path)  # nothing written yet
    headline.write(out, tmp_path)
    assert headline.check(out, tmp_path)
    write_run(
        run("recall", "20260929T140000Z", [recall_case(n, hit=True, ms=100) for n in range(40)]),
        tmp_path,
    )
    assert not headline.check(out, tmp_path)  # a newer run changes the page: regenerate it


def test_the_committed_file_matches_the_committed_runs() -> None:
    assert headline.check(), "run `make gen-client` to refresh frontend/public/headline.json"

"""The three numbers on the landing page, and the runs behind them (S4.13, FR-15.1, FR-15.3).

They come from the newest committed **live** run of each suite that was complete and big enough to
mean something: a run nobody can reproduce is an anecdote, and a number with no run says "no run
yet". Nothing is rounded up and nothing is hidden: a number that misses its target is shown as
measured (decision 11). The result is written to ``headline.json`` and checked in CI like the
OpenAPI snapshot, so the page can't drift from the run files.
"""

import json
from pathlib import Path
from typing import Any

from secondmind.config import DEFAULT_RESOURCES_DIR
from secondmind.evals.runs import RunRecord, load_run, pct
from secondmind.evals.spend import RUNS_DIR

# Where the landing page reads it: a static file of the web app.
HEADLINE_PATH = DEFAULT_RESOURCES_DIR.parent / "frontend" / "public" / "headline.json"

# A live run that touched only a few cases is a probe, not a measurement.
MIN_RECALL_CASES = 40
MIN_INGEST_CASES = 15


def _live_runs(suite: str, minimum: int, root: Path) -> list[RunRecord]:
    runs: list[RunRecord] = []
    for path in sorted((root / suite).glob("*.json")):
        if path.name.endswith(".fake.json"):
            continue
        run = load_run(path, root)
        if run.mode == "live" and run.status == "complete" and len(run.cases) >= minimum:
            runs.append(run)
    return runs


def _run_info(run: RunRecord) -> dict[str, Any]:
    models = sorted(set(run.models.values()))
    return {
        "id": run.run_id,
        "suite": run.suite,
        "date": run.started_at.date().isoformat(),
        "sha": run.git_sha[:7],
        "cases": len(run.cases),
        "passed": sum(c.passed for c in run.cases),
        "models": models,
    }


def _hit_rate(run: RunRecord) -> tuple[int, int]:
    gold = [c for c in run.cases if c.scores.get("has_gold")]
    return sum(bool(c.scores.get("hit5")) for c in gold), len(gold)


def _date_accuracy(run: RunRecord) -> tuple[int, int]:
    passed = total = 0
    for case in run.cases:
        passed += int(case.scores.get("passed", {}).get("date", 0))
        total += int(case.scores.get("total", {}).get("date", 0))
    return passed, total


def _p95_seconds(run: RunRecord) -> float | None:
    latencies = [float(c.latency_ms) for c in run.cases if c.fresh and c.latency_ms is not None]
    value = pct(latencies, 0.95)
    return None if value is None else value / 1000


def _metric(
    id_: str,
    label: str,
    *,
    run: RunRecord | None = None,
    value: float | None = None,
    display: str = "",
    detail: str = "",
) -> dict[str, Any]:
    if run is None or value is None:
        return {
            "id": id_,
            "label": label,
            "value": None,
            "display": "no run yet",
            "detail": detail,
            "run": None,
        }
    return {
        "id": id_,
        "label": label,
        "value": round(value, 4),
        "display": display,
        "detail": detail,
        "run": run.run_id,
    }


def headline(root: Path = RUNS_DIR) -> dict[str, Any]:
    """The headline numbers and the runs they come from, from the committed run files."""
    recall = _live_runs("recall", MIN_RECALL_CASES, root)
    ingest = _live_runs("ingest", MIN_INGEST_CASES, root)
    r = recall[-1] if recall else None
    i = ingest[-1] if ingest else None
    metrics: list[dict[str, Any]] = []
    if r is not None:
        hit, of = _hit_rate(r)
        rate = hit / of if of else None
        p95 = _p95_seconds(r)
        metrics.append(
            _metric(
                "retrieval_hit_rate",
                "Retrieval hit rate",
                run=r,
                value=rate,
                display=f"{100 * rate:.0f}%" if rate is not None else "",
                detail=f"hit@5: the answer's source was in the top five for {hit} of {of} "
                "questions with a known answer. Target 85%.",
            )
        )
    else:
        metrics.append(_metric("retrieval_hit_rate", "Retrieval hit rate"))
    if i is not None:
        ok, of = _date_accuracy(i)
        acc = ok / of if of else None
        metrics.append(
            _metric(
                "relative_date_accuracy",
                "Relative-date accuracy",
                run=i,
                value=acc,
                display=f"{100 * acc:.0f}%" if acc is not None else "",
                detail=f"{ok} of {of} dates worked out from words like 'last night' or "
                "'next May' were right.",
            )
        )
    else:
        metrics.append(_metric("relative_date_accuracy", "Relative-date accuracy"))
    if r is not None:
        p95 = _p95_seconds(r)
        metrics.append(
            _metric(
                "recall_p95_latency_s",
                "p95 time to answer a question",
                run=r,
                value=p95,
                display=f"{p95:.1f} s" if p95 is not None else "",
                detail="From sending a question to the end of the answer, over the recall set. "
                "Target 6 s; recall is being made faster.",
            )
        )
    else:
        metrics.append(_metric("recall_p95_latency_s", "p95 time to answer a question"))
    used = {m["run"] for m in metrics if m["run"]}
    runs = [_run_info(run) for run in (r, i) if run is not None and run.run_id in used]
    return {"schema_version": 1, "metrics": metrics, "runs": runs}


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def write(path: Path = HEADLINE_PATH, root: Path = RUNS_DIR) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(headline(root)), encoding="utf-8")
    return path


def check(path: Path = HEADLINE_PATH, root: Path = RUNS_DIR) -> bool:
    """Whether the file on disk is what the run files say now."""
    return path.exists() and path.read_text(encoding="utf-8") == render(headline(root))

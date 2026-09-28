"""Stamped run records (R.2), the seed of S5's ``/evals``: a run nobody can reproduce is an
anecdote.

Every eval run, fake or live, writes ``evals/runs/<suite>/<utc>-<shortsha>.json`` with what it
ran on (git SHA, config hash, prompt versions, the model per step, price version, provider mode,
the fixture's now and timezone) and, per case, the result, scores, latency, tokens and cost.
Live runs are committed; fake and dry runs end in ``.fake.json`` and are gitignored. No keys and
no environment dump: :func:`looks_like_secret` guards every write.

The tables :func:`render` prints are worked out from the stored per-case numbers, never from
logs, so a report of an old run is the report it had.
"""

import json
import re
import statistics
import subprocess
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from secondmind.config import DEFAULT_RESOURCES_DIR
from secondmind.evals.spend import RUNS_DIR

Mode = Literal["live", "fake", "dry-run"]
Status = Literal["complete", "stopped: budget", "stopped: error"]

# Anything shaped like a provider key, a Langfuse key or a bearer token.
_SECRET = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|sk-ant-[A-Za-z0-9_-]{8,}|pk-lf-[A-Za-z0-9-]{8,}|"
    r"sk-lf-[A-Za-z0-9-]{8,}|Bearer\s+[A-Za-z0-9._-]{16,}|AKIA[0-9A-Z]{16})"
)


def looks_like_secret(text: str) -> str | None:
    match = _SECRET.search(text)
    return match.group(0)[:8] + "…" if match else None


class CaseRecord(BaseModel):
    """One case of a run, as stored."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str = ""
    tags: list[str] = Field(default_factory=list)
    passed: bool
    failures: list[str] = Field(default_factory=list)
    scores: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int | None = None
    ttft_ms: int | None = None
    calls: list[dict[str, Any]] = Field(default_factory=list)
    cost_usd: float = 0.0
    spent_usd: float = 0.0
    cache_hits: int = 0
    detail: dict[str, Any] = Field(default_factory=dict)

    @property
    def fresh(self) -> bool:
        """Every call the case timed went to a provider: its latency means something."""
        timed = [c for c in self.calls if c.get("timed", True)]
        return bool(timed) and not any(c.get("cache_hit") or c.get("estimated") for c in timed)

    @property
    def tokens(self) -> dict[str, int]:
        out = {"input": 0, "cached_input": 0, "output": 0}
        for c in self.calls:
            out["input"] += int(c.get("input_tokens", 0))
            out["cached_input"] += int(c.get("cached_input_tokens", 0))
            out["output"] += int(c.get("output_tokens", 0))
        return out


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    run_id: str
    suite: str
    mode: Mode
    routing: str
    started_at: datetime
    finished_at: datetime | None = None
    git_sha: str
    git_dirty: bool
    config_hash: str
    price_version: str
    prompt_versions: dict[str, str | None]
    models: dict[str, str]
    fallbacks: dict[str, str | None]
    fixture: dict[str, str] = Field(default_factory=dict)
    selection: dict[str, Any] = Field(default_factory=dict)
    batch: str | None = None
    budget_usd: float | None = None
    estimate_usd: float | None = None
    status: Status = "complete"
    note: str | None = None
    # Calls made before the first case (seeding a fixture's embeddings, say).
    setup_calls: list[dict[str, Any]] = Field(default_factory=list)
    cases: list[CaseRecord] = Field(default_factory=list)

    @property
    def setup_cost_usd(self) -> float:
        return sum(float(c["cost_usd"]) for c in self.setup_calls)

    @property
    def spent_usd(self) -> float:
        setup = sum(
            float(c["cost_usd"])
            for c in self.setup_calls
            if c.get("billed") and not c.get("cache_hit")
        )
        return setup + sum(c.spent_usd for c in self.cases)

    @property
    def cost_usd(self) -> float:
        return self.setup_cost_usd + sum(c.cost_usd for c in self.cases)

    @property
    def is_committed(self) -> bool:
        return self.mode == "live"


def git_state(cwd: Path = DEFAULT_RESOURCES_DIR) -> tuple[str, bool]:
    """Short SHA of HEAD and whether code outside the run files differs from it."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", ".", ":(exclude)evals/runs"],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown", True
    return sha or "unknown", bool(status)


def new_run_id(sha: str, at: datetime | None = None) -> str:
    stamp = (at or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{sha[:7]}"


def run_path(run: RunRecord, root: Path = RUNS_DIR) -> Path:
    suffix = ".json" if run.is_committed else ".fake.json"
    return root / run.suite / f"{run.run_id}{suffix}"


def write_run(run: RunRecord, root: Path = RUNS_DIR) -> Path:
    body = run.model_dump_json(indent=2)
    leaked = looks_like_secret(body)
    if leaked:
        raise ValueError(f"refusing to write a run file with something like a key in it ({leaked})")
    path = run_path(run, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body + "\n", encoding="utf-8")
    return path


def load_run(ref: str | Path, root: Path = RUNS_DIR) -> RunRecord:
    """A run by path, or by run id (a prefix is enough when it's unique)."""
    path = Path(ref)
    if not path.exists():
        found = sorted(p for p in root.glob("*/*.json") if p.name.startswith(str(ref)))
        if len(found) != 1:
            raise FileNotFoundError(
                f"no single run matches {ref!r}" + (f": {[p.name for p in found]}" if found else "")
            )
        path = found[0]
    return RunRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))


def latest_runs(root: Path = RUNS_DIR, *, live_only: bool = True) -> list[Path]:
    """The newest run file of each suite."""
    out = []
    for suite in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        files = sorted(
            p for p in suite.glob("*.json") if not live_only or not p.name.endswith(".fake.json")
        )
        if files:
            out.append(files[-1])
    return out


# ------------------------------------------------------------------ metrics


@dataclass(frozen=True, slots=True)
class Metric:
    section: str
    label: str
    value: float | None
    fmt: str = "num"  # num | pct | ms | usd | int | frac
    total: float | None = None  # the denominator of a frac

    def text(self) -> str:
        v = self.value
        if v is None:
            return "n/a"
        if self.fmt == "frac":
            share = f" ({100 * v / self.total:.0f}%)" if self.total else ""
            return f"{v:.0f}/{self.total or 0:.0f}{share}"
        return _FORMATS.get(self.fmt, "{:.2f}").format(v * 100 if self.fmt == "pct" else v)

    @property
    def comparable(self) -> float | None:
        if self.value is None:
            return None
        if self.fmt == "frac":
            return self.value / self.total if self.total else None
        return self.value


_FORMATS = {"pct": "{:.1f}%", "ms": "{:.0f} ms", "usd": "${:.5f}", "int": "{:.0f}"}


def pct(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def _common(run: RunRecord) -> list[Metric]:
    cases = run.cases
    fresh = [c for c in cases if c.fresh]
    total = [float(c.latency_ms) for c in fresh if c.latency_ms is not None]
    ttft = [float(c.ttft_ms) for c in fresh if c.ttft_ms is not None]
    n = max(1, len(cases))
    by_step: dict[str, float] = defaultdict(float)
    for c in cases:
        for call in c.calls:
            by_step[str(call["step"])] += float(call["cost_usd"])
    per_case = sum(c.cost_usd for c in cases) / n
    rows = [
        Metric("cost", "cases", len(cases), "int"),
        Metric("cost", "cost per case", per_case, "usd"),
        *(
            Metric("cost", f"  {step}", cost / n, "usd")
            for step, cost in sorted(by_step.items(), key=lambda kv: -kv[1])
        ),
        Metric("cost", "setup (seeding)", run.setup_cost_usd or None, "usd"),
        Metric("cost", "spent this run", run.spent_usd, "usd"),
        Metric("cost", "cache hits", sum(c.cache_hits for c in cases), "int"),
        Metric("latency", "cases timed (no cache hits)", len(fresh), "int"),
        Metric("latency", "total p50", pct(total, 0.5), "ms"),
        Metric("latency", "total p95", pct(total, 0.95), "ms"),
    ]
    if ttft:
        rows += [
            Metric("latency", "first token p50", pct(ttft, 0.5), "ms"),
            Metric("latency", "first token p95", pct(ttft, 0.95), "ms"),
        ]
    return rows


def _intent(run: RunRecord) -> list[Metric]:
    cases = run.cases
    rows = [Metric("quality", "accuracy", sum(c.passed for c in cases), "frac", len(cases))]
    labels: dict[str, list[CaseRecord]] = defaultdict(list)
    for c in cases:
        labels[str(c.scores.get("expected"))].append(c)
    rows += [
        Metric("quality", f"  {label}", sum(c.passed for c in group), "frac", len(group))
        for label, group in sorted(labels.items())
    ]
    return rows


INGEST_FIELDS = ("kind", "state", "modality", "date", "entity", "category", "layer", "reconcile")


def _ingest(run: RunRecord) -> list[Metric]:
    rows = [
        Metric("quality", "cases passed", sum(c.passed for c in run.cases), "frac", len(run.cases))
    ]
    for name in INGEST_FIELDS:
        passed = sum(int(c.scores.get("passed", {}).get(name, 0)) for c in run.cases)
        total = sum(int(c.scores.get("total", {}).get(name, 0)) for c in run.cases)
        rows.append(Metric("fields", name, passed if total else None, "frac", total))
    return rows


def _recall(run: RunRecord) -> list[Metric]:
    cases = run.cases
    rows = [Metric("quality", "cases passed", sum(c.passed for c in cases), "frac", len(cases))]
    by_shape: dict[str, list[CaseRecord]] = defaultdict(list)
    for c in cases:
        if c.scores.get("has_gold"):
            by_shape[str(c.scores.get("shape", "?"))].append(c)
    for shape, group in sorted(by_shape.items()):
        hits = sum(bool(c.scores.get("hit5")) for c in group)
        mrr = statistics.mean(float(c.scores.get("rr", 0.0)) for c in group)
        rows.append(Metric("hit@5", shape, hits, "frac", len(group)))
        rows.append(Metric("MRR", shape, mrr, "num"))
    abstain = [c for c in cases if c.scores.get("must_abstain")]
    rows.append(
        Metric(
            "quality",
            "no-answer accuracy",
            sum(bool(c.scores.get("abstained")) for c in abstain),
            "frac",
            len(abstain),
        )
    )
    selected = sum(int(c.scores.get("selected", 0)) for c in cases)
    soft = sum(int(c.scores.get("soft_only", 0)) for c in cases)
    rows.append(
        Metric("quality", "soft-only share of selected", soft / selected if selected else None)
    )
    rows.append(
        Metric(
            "quality",
            "relaxed turns",
            sum(bool(c.scores.get("relaxed")) for c in cases),
            "frac",
            len(cases),
        )
    )
    return rows


def _smoke(run: RunRecord) -> list[Metric]:
    return [
        Metric("quality", "checks passed", sum(c.passed for c in run.cases), "frac", len(run.cases))
    ]


SUITE_METRICS: dict[str, Callable[[RunRecord], list[Metric]]] = {
    "intent": _intent,
    "ingest": _ingest,
    "recall": _recall,
    "smoke": _smoke,
}


def metrics(run: RunRecord) -> list[Metric]:
    """The run's metrics, grouped by section in the order sections first appear."""
    rows = [*SUITE_METRICS.get(run.suite, _smoke)(run), *_common(run)]
    order = list(dict.fromkeys(m.section for m in rows))
    return sorted(rows, key=lambda m: order.index(m.section))


def _header(run: RunRecord) -> list[str]:
    models = sorted(set(run.models.values()))
    return [
        f"{run.suite} · {run.run_id} · {run.mode} · routing {run.routing} · {run.status}",
        f"  git {run.git_sha}{' (dirty)' if run.git_dirty else ''} · config "
        f"{run.config_hash[:12]} · prices {run.price_version} · models {', '.join(models)}",
    ]


def render(run: RunRecord, *, failures: bool = True) -> str:
    lines = _header(run)
    section = None
    for m in metrics(run):
        if m.section != section:
            section = m.section
            lines.append(f"{section}")
        lines.append(f"  {m.label:<32} {m.text()}")
    if failures:
        failed = [c for c in run.cases if not c.passed]
        if failed:
            lines.append(f"failures ({len(failed)})")
            lines.extend(f"  {c.id}: {'; '.join(c.failures)[:300]}" for c in failed)
    return "\n".join(lines)


def render_pair(a: RunRecord, b: RunRecord) -> str:
    """Two runs side by side with deltas (B minus A)."""
    if a.suite != b.suite:
        return render(a) + "\n\n" + render(b)
    lines = [*(f"A {line}" for line in _header(a)), *(f"B {line}" for line in _header(b))]
    left = {(m.section, m.label): m for m in metrics(a)}
    right = {(m.section, m.label): m for m in metrics(b)}
    keys = list(dict.fromkeys([*left, *right]))
    section = None
    lines.append(f"{'':<34} {'A':>16} {'B':>16} {'Δ':>10}")
    for key in keys:
        if key[0] != section:
            section = key[0]
            lines.append(section)
        ma, mb = left.get(key), right.get(key)
        delta = ""
        if ma and mb and ma.comparable is not None and mb.comparable is not None:
            d = mb.comparable - ma.comparable
            fmt = ma.fmt
            if fmt in ("pct", "frac"):
                delta = f"{100 * d:+.1f} pt"
            elif fmt == "ms":
                delta = f"{d:+.0f} ms"
            elif fmt == "usd":
                delta = f"{d:+.5f}"
            else:
                delta = f"{d:+.2f}"
        ta = ma.text() if ma else "-"
        tb = mb.text() if mb else "-"
        lines.append(f"  {key[1]:<32} {ta:>16} {tb:>16} {delta:>10}")
    changed = _changed_cases(a, b)
    if changed:
        lines.append("cases that changed")
        lines.extend(f"  {line}" for line in changed)
    return "\n".join(lines)


def _changed_cases(a: RunRecord, b: RunRecord) -> list[str]:
    before = {c.id: c for c in a.cases}
    out = []
    for c in b.cases:
        prior = before.get(c.id)
        if prior is not None and prior.passed != c.passed:
            out.append(
                f"{c.id}: {'passed' if c.passed else 'failed'} (was "
                f"{'passed' if prior.passed else 'failed'})"
            )
    return out


def failed_ids(run: RunRecord) -> list[str]:
    return [c.id for c in run.cases if not c.passed]


def committed_run_files(root: Path = RUNS_DIR) -> Iterable[Path]:
    return (p for p in root.glob("*/*.json") if not p.name.endswith(".fake.json"))

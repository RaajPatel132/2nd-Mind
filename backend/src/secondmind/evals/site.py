"""A static page of every committed live eval run: pass rate, cost and latency per run, so the
evidence can be read without running the app (it is published with GitHub Pages).

It is generated from the stored run files and nothing else, one HTML file with its own CSS and
inline SVG: no script, no font or image from another site, nothing to go down. A run nobody can
reproduce is an anecdote, so every row names its run id and commit, and says whether the run
covered the whole suite or only a few cases picked to test an idea. The suites are small and
synthetic, and the page says so: it reports how the system did on those cases, no more.
"""

import html
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from secondmind.evals.runs import RunRecord, load_run, pct
from secondmind.evals.spend import RUNS_DIR

SUITES = ("recall", "ingest", "intent")
TITLES = {
    "recall": "Recall: finding and citing what was saved",
    "ingest": "Ingest: turning a message into the right memories",
    "intent": "Intent: telling a save from a question",
    "smoke": "Smoke: the production-shaped stack, end to end",
}
# Fewer fresh cases than this and a latency median would be a guess.
MIN_TIMED = 3


@dataclass(frozen=True, slots=True)
class Row:
    suite: str
    run_id: str
    day: date
    sha: str
    dirty: bool
    routing: str
    passed: int
    total: int
    cost_per_case: float
    spent: float
    p50_s: float | None
    p95_s: float | None
    hit5: tuple[int, int] | None
    status: str
    note: str
    full: bool

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


def collect(root: Path = RUNS_DIR) -> list[Row]:
    rows: list[Row] = []
    for suite_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for path in sorted(suite_dir.glob("*.json")):
            if not path.name.endswith(".fake.json"):
                run = load_run(path, root)
                if run.mode == "live":
                    rows.append(_row(run))
    return sorted(rows, key=lambda r: r.run_id)


def _row(run: RunRecord) -> Row:
    cases = run.cases
    fresh = [float(c.latency_ms) for c in cases if c.fresh and c.latency_ms is not None]
    gold = [c for c in cases if c.scores.get("has_gold")]
    return Row(
        suite=run.suite,
        run_id=run.run_id,
        day=run.started_at.date(),
        sha=run.git_sha[:7],
        dirty=run.git_dirty,
        routing=run.routing,
        passed=sum(c.passed for c in cases),
        total=len(cases),
        cost_per_case=sum(c.cost_usd for c in cases) / len(cases) if cases else 0.0,
        spent=run.spent_usd,
        p50_s=(pct(fresh, 0.5) or 0) / 1000 if len(fresh) >= MIN_TIMED else None,
        p95_s=(pct(fresh, 0.95) or 0) / 1000 if len(fresh) >= MIN_TIMED else None,
        hit5=(sum(bool(c.scores.get("hit5")) for c in gold), len(gold)) if gold else None,
        status=run.status,
        note=run.note or "",
        full=not run.selection,
    )


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


class Page:
    def __init__(self, rows: Sequence[Row], repo: str | None) -> None:
        self.rows = rows
        self.repo = repo

    # ------------------------------------------------------------------ links

    def commit(self, row: Row) -> str:
        label = _e(row.sha) + (" (dirty)" if row.dirty else "")
        if not self.repo:
            return label
        return f'<a href="https://github.com/{_e(self.repo)}/commit/{_e(row.sha)}">{label}</a>'

    def run_link(self, row: Row) -> str:
        label = f"<code>{_e(row.run_id)}</code>"
        if not self.repo:
            return label
        path = f"backend/evals/runs/{row.suite}/{row.run_id}.json"
        return f'<a href="https://github.com/{_e(self.repo)}/blob/main/{_e(path)}">{label}</a>'

    # ------------------------------------------------------------------ pieces

    def tiles(self) -> str:
        cells = []
        for suite in SUITES:
            latest = [r for r in self.rows if r.suite == suite and r.full]
            if not latest:
                continue
            r = latest[-1]
            hit = f"hit@5 {r.hit5[0]}/{r.hit5[1]} · " if r.hit5 else ""
            lat = f" · median {r.p50_s:.1f} s" if r.p50_s is not None else ""
            why = _e(r.status.removeprefix("stopped: "))
            stopped = (
                f"<br>stopped before the end of the suite ({why})" if r.status != "complete" else ""
            )
            cells.append(
                f'<div class="tile"><span class="k">{_e(suite)}, latest full run</span>'
                f'<span class="v">{r.passed}/{r.total}</span>'
                f'<span class="n">{hit}${r.cost_per_case:.4f} a case{lat}{stopped}<br>'
                f"{_e(r.day.isoformat())}, {self.commit(r)}</span></div>"
            )
        return f'<div class="tiles">{"".join(cells)}</div>' if cells else ""

    def chart(self, rows: Sequence[Row]) -> str:
        if len(rows) < 2:
            return ""
        width, height, pad = 640, 150, 28
        step = (width - 2 * pad) / (len(rows) - 1)

        def xy(i: int, r: Row) -> tuple[float, float]:
            return pad + i * step, pad + (1 - r.rate) * (height - 2 * pad)

        points = [xy(i, r) for i, r in enumerate(rows)]
        line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        grid = "".join(
            f'<line x1="{pad}" x2="{width - pad}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
            f'<text x="2" y="{y + 4:.1f}" class="axis">{label}</text>'
            for label, y in (("100%", pad), ("50%", height / 2), ("0", height - pad))
        )
        dots = "".join(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" '
            f'class="dot{"" if r.status == "complete" else " partial"}">'
            f"<title>{_e(r.day.isoformat())} · {r.passed}/{r.total} · "
            f"routing {_e(r.routing)}{'' if r.status == 'complete' else ' · ' + _e(r.status)}"
            "</title></circle>"
            for (x, y), r in zip(points, rows, strict=True)
        )
        said = ", ".join(f"{r.rate:.0%}" for r in rows)
        return (
            '<figure class="chart">'
            f'<svg viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="Pass rate of each full run, oldest to newest: {_e(said)}">'
            f'{grid}<polyline points="{line}" class="line"/>{dots}</svg>'
            f"<figcaption>Pass rate of each full run, oldest to newest: {_e(said)}. "
            "A hollow point is a run that stopped before the end of the suite. "
            "Runs differ in prompts and routing, so this is a record of work, not a trend line."
            "</figcaption></figure>"
        )

    def table(self, rows: Iterable[Row], *, latency: bool = True) -> str:
        head = (
            '<tr><th>Date</th><th>Run</th><th>Commit</th><th>Routing</th><th class="n">Passed</th>'
            '<th class="n">Cost a case</th>'
            + ('<th class="n">Median</th><th class="n">p95</th>' if latency else "")
            + "<th>Status</th></tr>"
        )
        body = []
        for r in rows:
            lat = (
                f'<td class="n">{"n/a" if r.p50_s is None else f"{r.p50_s:.1f} s"}</td>'
                f'<td class="n">{"n/a" if r.p95_s is None else f"{r.p95_s:.1f} s"}</td>'
                if latency
                else ""
            )
            status = "complete" if r.status == "complete" else r.status
            note = f'<span class="note">{_e(r.note)}</span>' if r.note else ""
            body.append(
                f"<tr><td>{_e(r.day.isoformat())}</td><td>{self.run_link(r)}</td>"
                f"<td>{self.commit(r)}</td><td>{_e(r.routing)}</td>"
                f'<td class="n">{r.passed}/{r.total}</td>'
                f'<td class="n">${r.cost_per_case:.4f}</td>{lat}'
                f'<td class="status">{_e(status)}{note}</td></tr>'
            )
        return f'<div class="scroll"><table>{head}{"".join(body)}</table></div>'

    def suite_section(self, suite: str) -> str:
        mine = [r for r in self.rows if r.suite == suite]
        if not mine:
            return ""
        full = [r for r in mine if r.full]
        subset = [r for r in mine if not r.full]
        parts = [
            f'<section aria-labelledby="s-{suite}"><h2 id="s-{suite}">{_e(TITLES[suite])}</h2>'
        ]
        if full:
            parts.append(self.chart(full))
            parts.append(
                f'<p class="lede">{len(full)} full-suite runs. A full run covers every case '
                "of the suite; one case is several points of the pass rate.</p>"
            )
            parts.append(self.table(full))
        if subset:
            parts.append(
                f"<details><summary>{len(subset)} runs on a few cases</summary>"
                '<p class="lede">Probes and re-reads to test one idea. They are not comparable '
                "with a full run.</p>"
                f"{self.table(subset)}</details>"
            )
        parts.append("</section>")
        return "".join(parts)

    def smoke_section(self) -> str:
        smoke = [r for r in self.rows if r.suite == "smoke"]
        if not smoke:
            return ""
        return (
            f'<section aria-labelledby="s-smoke"><h2 id="s-smoke">{_e(TITLES["smoke"])}</h2>'
            f"{self.table(smoke, latency=False)}</section>"
        )

    def render(self) -> str:
        total = len(self.rows)
        days = sorted({r.day for r in self.rows})
        span = f"{days[0].isoformat()} to {days[-1].isoformat()}" if days else "no runs yet"
        sections = "".join(self.suite_section(s) for s in SUITES) + self.smoke_section()
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>2nd Mind evaluation runs</title>
<meta name="description" content="Every committed live evaluation run of 2nd Mind: pass rate,
cost and latency, with the commit and the run file behind each.">
<style>{CSS}</style>
</head>
<body>
<main>
<header>
<p class="over">2nd Mind · evaluation runs</p>
<h1>What the system did on the cases it was measured on</h1>
<p class="lede">{total} live runs ({_e(span)}), each one a file in the repository, stamped with the
commit, the prompts, the models and the prices it ran with. The sets are small and synthetic: this
says how it did on those cases, not on yours, and not against a public benchmark. A number that
misses its target is shown as measured.</p>
</header>
{self.tiles()}
{sections}
<footer>
<p>Generated from the committed run files by <code>python -m secondmind.evals site</code>. Costs are
priced at list price, including calls replayed from the harness's cache. Latency is the median and
p95 of the cases in which every call went to a provider; with fewer than {MIN_TIMED} such cases it
is not shown.</p>
</footer>
</main>
</body>
</html>
"""


CSS = """
:root {
  color-scheme: dark light;
  --bg: #08080a; --surface: #101013; --line: #26262c;
  --fg: #f4f4f6; --fg2: #b4b4bc; --fg3: #8a8a94; --accent: #86d9ae;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #faf9f5; --surface: #fff; --line: #dcdad0;
    --fg: #141413; --fg2: #4a4a46; --fg3: #6f6e68; --accent: #1f7a4f;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg); padding: 40px 16px 64px;
  font: 15px/24px ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
}
main {
  max-width: 880px; margin: 0 auto; display: grid; gap: 44px;
  grid-template-columns: minmax(0, 1fr);
}
h1 {
  font: 400 34px/40px ui-serif, Georgia, serif; letter-spacing: -0.01em;
  margin: 4px 0 12px; text-wrap: balance;
}
h2 { font: 400 22px/30px ui-serif, Georgia, serif; margin: 0 0 12px; }
.over {
  font: 600 11px/16px ui-sans-serif, system-ui, sans-serif; margin: 0;
  letter-spacing: 0.12em; text-transform: uppercase; color: var(--fg3);
}
.lede { color: var(--fg2); max-width: 64ch; margin: 0 0 12px; }
a { color: inherit; text-underline-offset: 3px; text-decoration-color: var(--line); }
a:hover { text-decoration-color: var(--fg2); }
a:focus-visible, summary:focus-visible {
  outline: 2px solid var(--fg); outline-offset: 2px; border-radius: 4px;
}
code { font: 12px/18px ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--fg); }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.tile { background: var(--surface); border-radius: 14px; padding: 16px; display: grid; gap: 4px; }
.tile .k { font-weight: 500; color: var(--fg2); }
.tile .v {
  font: 500 30px/36px ui-sans-serif, system-ui, sans-serif; font-variant-numeric: tabular-nums;
}
.tile .n { font: 12px/18px ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--fg3); }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; line-height: 20px; }
th, td {
  text-align: left; padding: 8px 10px; border-top: 1px solid var(--line);
  white-space: nowrap; vertical-align: top;
}
th {
  font-weight: 600; color: var(--fg3); font-size: 11px;
  letter-spacing: 0.06em; text-transform: uppercase;
}
th.n, td.n { text-align: right; font-variant-numeric: tabular-nums; }
td.status { white-space: normal; min-width: 16ch; }
.note { display: block; color: var(--fg3); white-space: normal; max-width: 34ch; }
details { margin-top: 14px; }
summary { cursor: pointer; color: var(--fg2); }
.chart { margin: 0 0 14px; }
.chart svg { width: 100%; height: auto; display: block; }
.chart .grid { stroke: var(--line); stroke-width: 1; }
.chart .axis { fill: var(--fg3); font: 10px ui-monospace, Menlo, monospace; }
.chart .line { fill: none; stroke: var(--fg3); stroke-width: 1.5; }
.chart .dot { fill: var(--accent); }
.chart .partial { fill: var(--bg); stroke: var(--accent); stroke-width: 2; }
figcaption, footer { color: var(--fg3); font-size: 13px; line-height: 20px; max-width: 70ch; }
footer { border-top: 1px solid var(--line); padding-top: 16px; }
@media (max-width: 640px) {
  body { padding-top: 28px; }
  h1 { font-size: 28px; line-height: 34px; }
}
@media (prefers-reduced-motion: no-preference) { html { scroll-behavior: smooth; } }
"""


def build(out: Path, *, repo: str | None = None, root: Path = RUNS_DIR) -> Path:
    """Write ``index.html`` under ``out`` and return its path."""
    out.mkdir(parents=True, exist_ok=True)
    target = out / "index.html"
    target.write_text(Page(collect(root), repo).render(), encoding="utf-8")
    return target

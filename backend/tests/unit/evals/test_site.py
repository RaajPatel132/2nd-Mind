"""The static page of committed live runs: built from the run files alone, one self-contained file
that links only to the repository, and honest about runs that stopped or covered a few cases."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from secondmind.evals.runs import RUNS_DIR
from secondmind.evals.site import build, collect, coverage_badge, eval_badge

SHIPPED = "20260929T160542Z-1b15a10"


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.urls += [v for k, v in attrs if k in ("href", "src", "action") and v]


def parsed(page: str) -> Links:
    parser = Links()
    parser.feed(page)
    return parser


def case(passed: bool, ms: int | None = 5000) -> dict[str, Any]:
    call = {"step": "answer", "latency_ms": ms, "timed": True, "cost_usd": 0.01}
    return {
        "id": "c",
        "passed": passed,
        "latency_ms": ms,
        "cost_usd": 0.01,
        "spent_usd": 0.01,
        "calls": [call],
    }


def write_run(root: Path, name: str, **fields: Any) -> None:
    suite = fields.pop("suite", "recall")
    body = {
        "run_id": name, "suite": suite, "mode": "live", "routing": "economy-c",
        "started_at": "2026-09-29T10:00:00Z", "git_sha": "abcdef1234567", "git_dirty": False,
        "config_hash": "h", "price_version": "p", "prompt_versions": {}, "models": {},
        "fallbacks": {}, "cases": [case(True)] * 4 + [case(False)],
    } | fields  # fmt: skip
    (root / suite).mkdir(parents=True, exist_ok=True)
    (root / suite / f"{name}.json").write_text(json.dumps(body))


@pytest.fixture
def page(tmp_path: Path) -> str:
    return build(tmp_path / "out", repo="owner/name").read_text(encoding="utf-8")


def test_every_committed_live_run_is_on_the_page(page: str) -> None:
    live = [p for p in RUNS_DIR.glob("*/*.json") if not p.name.endswith(".fake.json")]
    assert len(live) >= 50
    assert len(collect()) == len(live)
    for path in live:
        assert path.stem in page, path.name


def test_the_shipped_routings_numbers_match_the_run_file(page: str) -> None:
    row = next(r for r in collect() if r.run_id == SHIPPED)
    assert (row.passed, row.total, row.routing) == (45, 51, "economy-c")
    assert row.hit5 == (37, 41)
    assert "45/51" in page
    assert "hit@5 37/41" in page


def test_the_page_has_no_script_and_links_only_to_the_repository(page: str) -> None:
    found = parsed(page)
    assert "script" not in found.tags
    assert "iframe" not in found.tags
    outside = [u for u in found.urls if not u.startswith("https://github.com/owner/name/")]
    assert outside == []
    assert not re.search(r"@import|url\(http", page)


def test_without_a_repo_there_are_no_links_at_all(tmp_path: Path) -> None:
    text = build(tmp_path / "out").read_text(encoding="utf-8")
    assert parsed(text).urls == []


def test_a_run_that_stopped_early_is_marked_in_the_tile_and_the_chart(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    write_run(root, "20260929T100000Z-aaaaaaa")
    write_run(root, "20260929T110000Z-bbbbbbb", status="stopped: budget")
    page = build(tmp_path / "out", root=root).read_text(encoding="utf-8")
    assert "stopped before the end of the suite (budget)" in page
    assert page.count('class="dot partial"') == 1
    assert 'class="dot"' in page


def test_a_run_on_a_few_cases_goes_under_the_fold_and_out_of_the_chart(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    write_run(root, "20260929T100000Z-aaaaaaa")
    write_run(root, "20260929T110000Z-bbbbbbb", selection={"cases": ["@probe"]})
    page = build(tmp_path / "out", root=root).read_text(encoding="utf-8")
    assert "1 runs on a few cases" in page
    assert page.index("20260929T100000Z-aaaaaaa") < page.index("<details>")
    assert page.index("<details>") < page.index("20260929T110000Z-bbbbbbb")


def test_text_from_a_run_file_is_escaped(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    write_run(root, "20260929T100000Z-aaaaaaa", note="<img src=x onerror=alert(1)> & more")
    page = build(tmp_path / "out", root=root).read_text(encoding="utf-8")
    assert "<img" not in page
    assert "&lt;img src=x onerror=alert(1)&gt; &amp; more" in page


def test_a_median_from_fewer_than_three_timed_cases_is_not_shown(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    write_run(root, "20260929T100000Z-aaaaaaa", cases=[case(True), case(True)])
    (row,) = collect(root)
    assert row.p50_s is None
    assert "n/a" in build(tmp_path / "out", root=root).read_text(encoding="utf-8")


def test_the_eval_badge_names_the_newest_full_recall_run(tmp_path: Path) -> None:
    badge = eval_badge(collect())
    assert badge is not None
    assert badge["message"] == "45/51"
    assert badge["color"] == "green"  # 88%
    assert badge["schemaVersion"] == 1
    out = tmp_path / "out"
    build(out)
    assert json.loads((out / "evals.json").read_text()) == badge


def test_a_recall_run_that_stopped_early_says_so_in_its_badge(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    write_run(root, "20260929T100000Z-aaaaaaa", status="stopped: budget")
    badge = eval_badge(collect(root))
    assert badge is not None
    assert badge["message"] == "4/5 (stopped early)"


def test_no_recall_run_means_no_badge(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    write_run(root, "20260929T100000Z-aaaaaaa", suite="intent")
    build(tmp_path / "out", root=root)
    assert not (tmp_path / "out" / "evals.json").exists()


@pytest.mark.parametrize(
    ("percent", "color"),
    [(95.0, "brightgreen"), (84.2, "green"), (75.9, "yellowgreen"), (61, "yellow"), (30, "orange")],
)
def test_the_coverage_badge_rounds_and_colours_a_coverage_py_report(
    percent: float, color: str
) -> None:
    badge = coverage_badge({"totals": {"percent_covered": percent}})
    assert badge == {
        "schemaVersion": 1,
        "label": "unit coverage",
        "message": f"{percent:.0f}%",
        "color": color,
    }


def test_the_page_build_writes_the_coverage_badge_when_given_a_report(tmp_path: Path) -> None:
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"totals": {"percent_covered": 75.94}}))
    out = tmp_path / "out"
    build(out, coverage_report=report)
    assert json.loads((out / "coverage.json").read_text())["message"] == "76%"

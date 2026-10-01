"""The README's numbers are the recorded runs' numbers, generated, never typed in.

`scripts/sync_readme_numbers.py` renders the block from `frontend/public/headline.json` (which
`make headline-check` ties to the committed live runs). If this test fails after a new run,
run `python3 scripts/sync_readme_numbers.py` and commit the README.
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).parents[3]
README = ROOT / "README.md"
HEADLINE = json.loads((ROOT / "frontend" / "public" / "headline.json").read_text())


def _script() -> ModuleType:
    path = ROOT / "scripts" / "sync_readme_numbers.py"
    spec = importlib.util.spec_from_file_location("sync_readme_numbers", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = _script()


def test_the_readme_shows_the_recorded_numbers() -> None:
    text = README.read_text()
    assert SYNC.sync(text, HEADLINE) == text, (
        "README.md's numbers differ from frontend/public/headline.json: "
        "run python3 scripts/sync_readme_numbers.py"
    )


def test_every_number_on_the_front_page_is_in_the_readme() -> None:
    text = README.read_text()
    for metric in HEADLINE["metrics"]:
        assert metric["label"] in text
        if metric["run"] is not None:
            assert metric["display"] in text
            assert metric["run"] in text


def test_every_run_the_readme_links_exists_in_the_repository() -> None:
    for run in HEADLINE["runs"]:
        assert (ROOT / "backend" / "evals" / "runs" / run["suite"] / f"{run['id']}.json").is_file()


def test_a_metric_with_no_run_says_so_instead_of_a_number() -> None:
    headline: dict[str, Any] = {
        "metrics": [{"label": "Recall p95", "display": "9.9 s", "detail": "x", "run": None}],
        "runs": [],
    }
    block = SYNC.render(headline)
    assert "| Recall p95 | no run yet | | |" in block
    assert "9.9 s" not in block


def test_the_block_markers_must_appear_exactly_once() -> None:
    with pytest.raises(SystemExit):
        SYNC.sync("no markers here", HEADLINE)
    with pytest.raises(SystemExit):
        SYNC.sync(SYNC.START + SYNC.END + SYNC.START + SYNC.END, HEADLINE)

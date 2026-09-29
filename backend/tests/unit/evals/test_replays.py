"""The replay recorder (R.5): a live run's outputs become a case's ``model:`` block, and only that
block changes."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from secondmind.evals import replays
from secondmind.evals.calls import _context_of
from secondmind.evals.runs import CaseRecord
from secondmind.providers import ChatMessage
from secondmind.providers.contract import AdapterRequest


def _case(*calls: dict[str, Any]) -> CaseRecord:
    return CaseRecord(id="01-x", passed=True, calls=list(calls))


def test_the_last_output_of_each_step_is_the_one_recorded() -> None:
    case = _case(
        {"step": "intent", "output": {"intent": "save", "confidence": 0.9, "reason": "a"}},
        {"step": "extract", "output": {"memories": [{"ref": "m1"}]}, "failed": "invalid"},
        {"step": "extract", "output": {"memories": [{"ref": "m2"}]}},
        {"step": "embed"},
    )
    replay = replays.recorded_model(case, "ingest")
    assert replay.steps == ["intent", "extract"]
    assert replay.model["extract"] == {"memories": [{"ref": "m2"}]}


def test_a_repeated_resolve_keeps_every_output_in_order() -> None:
    case = _case(
        {"step": "resolve", "output": {"choice": "a"}},
        {"step": "resolve", "output": {"choice": "b"}},
    )
    assert replays.recorded_model(case, "ingest").model["resolve"] == [
        {"choice": "a"},
        {"choice": "b"},
    ]


def test_a_rerank_is_pinned_to_the_words_of_each_candidate() -> None:
    context = {
        "c1": {"text": "I live in Pune", "state": "current"},
        "c2": {"text": "I lived in Bengaluru", "state": "superseded"},
    }
    output = {
        "scores": [
            {"id": "C1", "score": 1.0, "reason": "now"},
            {"id": "c2", "score": 0.2, "reason": ""},
            {"id": "c9", "score": 0.5, "reason": ""},  # not in the request: left out
        ]
    }
    case = _case({"step": "rerank", "output": output, "context": context})
    pins = replays.recorded_model(case, "recall").model["rerank"]
    assert pins == [
        {"match": "I live in Pune", "score": 1.0, "state": "current"},
        {"match": "I lived in Bengaluru", "score": 0.2, "state": "superseded"},
    ]


def test_pruning_drops_empty_fields_but_keeps_a_reminder_at_the_default_lead() -> None:
    pruned = replays.prune(
        {"title": "x", "summary": None, "tags": [], "reminder": {"lead": None}, "n": False}
    )
    assert pruned == {"title": "x", "reminder": {}}


def test_rewriting_replaces_the_model_block_and_nothing_else(tmp_path: Path) -> None:
    path = tmp_path / "01-x.yaml"
    path.write_text(
        "# a comment\nid: 01-x\ninput: hello\nmodel:\n  intent: {intent: save}\n"
        "expect:\n  intent: save\n",
        encoding="utf-8",
    )
    replays.rewrite_model(path, {"intent": {"intent": "recall", "confidence": 0.5}})
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# a comment\nid: 01-x\ninput: hello\nmodel:\n")
    assert text.endswith("expect:\n  intent: save\n")
    assert yaml.safe_load(text)["model"] == {"intent": {"intent": "recall", "confidence": 0.5}}


def test_comparing_flags_only_the_steps_that_differ(tmp_path: Path) -> None:
    path = tmp_path / "01-x.yaml"
    path.write_text(
        "id: 01-x\nmodel:\n  intent: {intent: save, confidence: 0.9, reason: a}\n"
        "  extract: {memories: []}\nexpect: {}\n",
        encoding="utf-8",
    )
    case = _case(
        {"step": "intent", "output": {"intent": "save", "confidence": 0.9, "reason": "a"}},
        {"step": "extract", "output": {"memories": [{"ref": "m1"}]}},
    )
    (change,) = replays.compare(SimpleNamespace(cases=[case]), "ingest", tmp_path)  # type: ignore[arg-type]
    assert change.steps == ["extract"]
    assert "m1" in change.diff


def test_a_rerank_request_names_its_candidates_by_label() -> None:
    body = {
        "question": "where do I live",
        "candidates": [
            {"id": "C1", "text": "x" * 200, "state": "current"},
            {"id": "C2", "text": "short", "state": None},
        ],
    }
    request = AdapterRequest(
        step="rerank",
        model="m",
        system=None,
        messages=[ChatMessage(role="user", content=json.dumps(body))],
        max_output_tokens=10,
    )
    context = _context_of(request)
    assert context is not None
    assert context["c1"] == {"text": "x" * 80, "state": "current"}
    assert context["c2"] == {"text": "short", "state": None}
    other = AdapterRequest(step="plan", model="m", system=None, messages=[], max_output_tokens=1)
    assert _context_of(other) is None

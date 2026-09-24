"""The offline "brain" for fake-provider mode (local dev without keys, and the E2E suite).

When a message matches a golden ingestion case (``evals/cases/ingest``), the fake replays the
model outputs recorded in that case, so the demo and E2E see realistic saves. Anything else gets
a simple, honest heuristic: greetings are chit-chat, questions are recall, everything else is
saved as a note. None of this runs when a real provider serves the step.
"""

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from secondmind.providers import AdapterRequest, Responder

_GREETING = re.compile(
    r"^(hi|hello|hey|hiya|thanks|thank you|cheers|good (morning|afternoon|evening|night)|"
    r"how are you|what can you do)\b"
)
_QUESTION = re.compile(
    r"^(what|when|where|who|which|how|why|do i|did i|have i|am i|is|are|was|were|can you find|"
    r"remind me what|tell me what)\b"
)
_CORRECTION = re.compile(r"^(no[,. ]|nope|actually[, ]|that's wrong|wrong[,. ]|correct that)")


def load_replay(directory: Path) -> dict[str, dict[str, Any]]:
    """Map each golden case's input (and its setup inputs) to the model outputs it recorded."""
    replay: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return replay
    for path in sorted(directory.glob("*.yaml")):
        case = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for turn in [*case.get("setup", []), case]:
            message, model = turn.get("input"), turn.get("model")
            if isinstance(message, str) and isinstance(model, dict):
                replay.setdefault(replay_key(message), model)
    return replay


def offline_responders(
    replay: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Responder]:
    replay = replay or {}
    counters: dict[tuple[str, str], int] = {}

    def recorded(step: str, request: AdapterRequest) -> dict[str, Any] | None:
        model = replay.get(replay_key(_message(request)))
        if model is None or step not in model:
            return None
        output = model[step]
        if isinstance(output, list):
            n = counters.get((step, replay_key(_message(request))), 0)
            counters[(step, replay_key(_message(request)))] = n + 1
            return dict(output[min(n, len(output) - 1)]) if output else None
        return dict(output)

    def intent(request: AdapterRequest) -> dict[str, Any]:
        return recorded("intent", request) or heuristic_intent(_message(request))

    def extract(request: AdapterRequest) -> dict[str, Any]:
        found = recorded("extract", request)
        return complete_extract(found) if found is not None else heuristic_note(_message(request))

    def enrich(request: AdapterRequest) -> dict[str, Any]:
        found = recorded("enrich", request)
        if found is not None:
            return found
        try:
            listing = json.loads(request.messages[-1].content)
        except (ValueError, IndexError):
            listing = []
        refs = [m.get("ref") for m in listing if isinstance(m, dict)]
        return {"memories": [{"ref": r, "alt": [], "cues": []} for r in refs if r]}

    def resolve(request: AdapterRequest) -> dict[str, Any]:
        return recorded("resolve", request) or {
            "candidate": "c1",
            "reason": "offline fake: took the most recent match",
        }

    def reconcile(request: AdapterRequest) -> dict[str, Any]:
        return recorded("reconcile", request) or {
            "decision": "new",
            "candidate": None,
            "reason": "offline fake: kept it separate",
        }

    return {
        "intent": intent,
        "extract": extract,
        "enrich": enrich,
        "resolve": resolve,
        "reconcile": reconcile,
    }


_MEMORY_DEFAULTS: dict[str, Any] = {
    "subtype": None,
    "format": None,
    "state": None,
    "summary": None,
    "category": None,
    "tags": [],
    "attributes": [],
    "subject": None,
    "predicate": None,
    "value": None,
    "entities": [],
    "times": [],
    "reminder": None,
    "modality": "asserted",
    "sentiment": None,
    "rating": None,
    "sensitivity": "normal",
    "importance": 3,
    "links": [],
    "layer": "archive",
    "layer_rationale": "",
    "rationale": "",
}


def complete_extract(data: Mapping[str, Any]) -> dict[str, Any]:
    """Fill the fields a recorded case leaves out, so case files stay short. (A real model
    returns every field: the providers' strict structured output enforces it.)"""
    out: dict[str, Any] = {
        "entities": [{"name": None, "label": None, **e} for e in data.get("entities", [])],
        "relations": [{"evidence": None, **r} for r in data.get("relations", [])],
        "not_written": list(data.get("not_written", [])),
        "secret_spans": list(data.get("secret_spans", [])),
        "memories": [],
    }
    for memory in data.get("memories", []):
        m = {**_MEMORY_DEFAULTS, **memory}
        if isinstance(m["attributes"], Mapping):
            m["attributes"] = [{"key": k, "value": str(v)} for k, v in m["attributes"].items()]
        m["times"] = [{"direction": None, "recurring": False, **t} for t in m["times"]]
        if isinstance(m["value"], Mapping):
            m["value"] = {"text": None, "number": None, "unit": None, **m["value"]}
        out["memories"].append(m)
    return out


def heuristic_intent(message: str) -> dict[str, Any]:
    text = message.strip().lower()
    if _GREETING.match(text):
        intent, reason = "chit_chat", "a greeting or small talk"
    elif _CORRECTION.match(text):
        intent, reason = "correct", "it corrects something"
    elif text.endswith("?") or _QUESTION.match(text):
        intent, reason = "recall", "it asks about something"
    else:
        intent, reason = "save", "it tells me something to keep"
    return {"intent": intent, "confidence": 0.6, "reason": f"offline fake: {reason}"}


def heuristic_note(message: str) -> dict[str, Any]:
    text = " ".join(message.split())
    title = text if len(text) <= 60 else text[:59] + "…"
    return {
        "entities": [],
        "memories": [
            {
                "ref": "m1",
                "kind": "note",
                "subtype": None,
                "format": None,
                "state": None,
                "title": title,
                "text": text,
                "summary": None,
                "category": "notes",
                "tags": [],
                "attributes": [],
                "subject": None,
                "predicate": None,
                "value": None,
                "entities": [],
                "times": [],
                "reminder": None,
                "modality": "asserted",
                "sentiment": None,
                "rating": None,
                "sensitivity": "normal",
                "importance": 2,
                "links": [],
                "layer": "archive",
                "layer_rationale": "offline fake: every unknown message is a note",
                "rationale": "offline fake provider: no model understood this message",
            }
        ],
        "relations": [],
        "not_written": [],
        "secret_spans": [],
    }


def _message(request: AdapterRequest) -> str:
    users = [m.content for m in request.messages if m.role == "user"]
    return users[0] if users else ""


def replay_key(message: str) -> str:
    return " ".join(message.lower().split())


def iter_case_files(directory: Path) -> Iterable[Path]:
    return sorted(directory.glob("*.yaml")) if directory.is_dir() else []

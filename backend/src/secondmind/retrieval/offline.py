"""The offline "brain" for recall in fake-provider mode (local dev without keys, E2E, goldens).

When a question matches a recall golden case (``evals/cases/retrieval``), the fake replays the
plan recorded in it (and any rerank scores it pins). Anything else gets honest heuristics: a
``semantic`` plan over the whole question, rerank scores from word overlap, and an answer
stitched from the evidence lines of the context pack, with their ``[n]`` markers. None of
this runs when a real provider serves the step.
"""

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from secondmind.providers import AdapterRequest, Responder, TextResponder
from secondmind.retrieval.plan import fallback_plan

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    {
        "a", "about", "am", "an", "and", "any", "anything", "are", "as", "at", "be", "been",
        "can", "could", "currently", "did", "do", "does", "doing", "else", "for", "from",
        "give", "had", "has", "have", "how", "i", "if", "in", "is", "it", "just", "list",
        "many", "me", "much", "my", "now", "of", "on", "or", "should", "show", "so", "tell",
        "that", "the", "their", "them", "there", "this", "to", "was", "we", "were", "what",
        "whats", "when", "where", "which", "who", "why", "will", "with", "would", "you",
        "your", "s",
    }
)  # fmt: skip
_EVIDENCE = re.compile(r'^\[(\d+)\] ([^·]+?) · (.+?) · "(.*?)"')
_SAID = re.compile(r'^\[(\d+)\] (I said|You said) on (.+?), in the chat .*?: "(.*)"$')


def replay_key(text: str) -> str:
    return " ".join(text.lower().split())


def load_recall_replay(directory: Path) -> dict[str, dict[str, Any]]:
    """Each recall case's question (and its setup questions) -> the outputs it recorded."""
    replay: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return replay
    for path in sorted(directory.glob("*.yaml")):
        case = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for turn in [*case.get("setup", []), case]:
            question, model = turn.get("question") or turn.get("input"), turn.get("model")
            if isinstance(question, str) and isinstance(model, dict):
                replay.setdefault(replay_key(question), model)
    return replay


def recall_responders(
    replay: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Responder]:
    replay = replay or {}

    def plan(request: AdapterRequest) -> dict[str, Any]:
        question = _last_user(request)
        recorded = replay.get(replay_key(question), {}).get("plan")
        if isinstance(recorded, Mapping):
            return complete_plan(recorded)
        return fallback_plan(question).model_dump(mode="json")

    def rerank(request: AdapterRequest) -> dict[str, Any]:
        try:
            body = json.loads(_last_user(request))
        except ValueError:
            return {"scores": []}
        question = str(body.get("question", ""))
        pins = replay.get(replay_key(question), {}).get("rerank") or []
        scores = []
        for c in body.get("candidates", []):
            text = str(c.get("text", ""))
            pinned = next(
                (p for p in pins if str(p.get("match", "")).lower() in text.lower()), None
            )
            score = float(pinned["score"]) if pinned else overlap(question, text)
            why = "offline: pinned by the case" if pinned else "offline: shared words"
            scores.append({"id": c.get("id", ""), "score": round(score, 3), "reason": why})
        return {"scores": scores}

    return {"plan": plan, "rerank": rerank}


def recall_text_responders() -> dict[str, TextResponder]:
    return {"answer": offline_answer}


def overlap(question: str, text: str) -> float:
    """Share of the question's content words found in the text (lightly singularised)."""
    wanted = _content(question)
    if not wanted:
        return 0.0
    return len(wanted & _content(text)) / len(wanted)


def _content(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP}


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def offline_answer(request: AdapterRequest) -> str | None:
    """A reply stitched from the pack's evidence lines, citing each; None for chit-chat."""
    system = request.system or ""
    if "Evidence:" not in system:
        return None
    parts: list[str] = []
    for block in re.split(r"\n(?=Question \d+ \()", system):
        if not block.startswith("Question "):
            continue
        sentences: list[str] = []
        exact = re.search(r"Exact result \((\w+), from the database\): ([\d.]+)", block)
        counted = [m.group(1) for m in re.finditer(r"^\[(\d+)\] .*· counted", block, re.M)]
        if exact:
            cites = "".join(f"[{n}]" for n in counted[:12])
            sentences.append(f"The {exact.group(1)} is {exact.group(2)} {cites}.")
        for line in block.splitlines():
            said = _SAID.match(line)
            if said:
                sentences.append(
                    f"{said.group(2)} on {said.group(3)}: {said.group(4)[:240]} [{said.group(1)}]"
                )
                continue
            ev = _EVIDENCE.match(line)
            if ev and "· counted" not in line:
                text = ev.group(4).rstrip(".")
                sentences.append(f"{text} ({ev.group(3)}) [{ev.group(1)}].")
            if len(sentences) >= 4:
                break
        none = re.search(r"Evidence: none\. Say plainly: (.+)$", block, re.M)
        if none:
            sentences.append(none.group(1))
        if sentences:
            parts.append(" ".join(sentences))
    return "From what you've saved: " + "\n\n".join(parts) if parts else None


_PLAN_DEFAULTS: dict[str, Any] = {
    "about": None,
    "times": [],
    "entities": [],
    "aggregate": None,
    "set": None,
    "role": None,
    "about_sensitive": False,
    "expanded_from": None,
}
_FILTER_DEFAULTS: dict[str, Any] = {
    "kinds": [],
    "subtypes": [],
    "states": [],
    "category": None,
    "predicate": None,
    "attribute": None,
    "role": None,
    "current_only": False,
}


def complete_plan(data: Mapping[str, Any]) -> dict[str, Any]:
    """Fill what a recorded plan leaves out, so case files stay short. (A real model returns
    every field: strict structured output enforces it.)"""
    subs = []
    for sq in data.get("sub_queries", []):
        full = {**_PLAN_DEFAULTS, **sq}
        full["filters"] = {**_FILTER_DEFAULTS, **(sq.get("filters") or {})}
        full["times"] = [{"direction": "any", "anchor": None, **t} for t in full["times"]]
        full["entities"] = [{"name": None, "path": [], **e} for e in full["entities"]]
        if isinstance(full["aggregate"], Mapping):
            full["aggregate"] = {"field": None, "group_by": None, **full["aggregate"]}
        if isinstance(full["set"], Mapping):
            full["set"] = {"other_kind": None, "entity": None, **full["set"]}
        full.setdefault("topic", full.get("question", ""))
        subs.append(full)
    return {"sub_queries": subs}


def _last_user(request: AdapterRequest) -> str:
    users = [m.content for m in request.messages if m.role == "user"]
    return users[-1] if users else ""

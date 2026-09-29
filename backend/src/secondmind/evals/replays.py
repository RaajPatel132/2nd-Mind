"""Replay fixtures from reality (R.5): a live run's recorded model outputs, in the fake provider's
replay format, so the offline suites describe what real models do.

Each golden case file carries a ``model:`` block, the outputs the fake provider replays for that
case's input. ``make record-replays`` rebuilds that block from a stamped run file (the outputs
were recorded per call, R.2), shows what changed case by case, and rewrites only that block
when asked: the rest of the file (the expectations, the comments) is left as it is.
"""

import difflib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from secondmind.evals.runs import CaseRecord, RunRecord

# The steps whose outputs a case's replay carries, per suite.
STEPS: dict[str, tuple[str, ...]] = {
    "ingest": ("intent", "extract", "resolve", "reconcile", "enrich"),
    "recall": ("intent", "plan", "rerank"),
}
# Steps that can repeat inside a turn with different inputs: every output is kept, in order.
SEQUENCES = frozenset({"resolve", "reconcile"})


@dataclass(frozen=True, slots=True)
class CaseReplay:
    """What recording found for one case."""

    case_id: str
    model: dict[str, Any]
    steps: list[str]  # the steps that were recorded


def recorded_model(case: CaseRecord, suite: str) -> CaseReplay:
    """The ``model:`` block a case's live calls describe. A step that ran twice (a reply the
    pipeline sent back for correction) keeps the last output, the one that was used."""
    wanted = STEPS[suite]
    found: dict[str, list[Any]] = {}
    for call in case.calls:
        step = call.get("step")
        output = call.get("output")
        if step not in wanted or output is None or call.get("failed"):
            continue
        if step == "rerank":
            output = rerank_pins(call.get("context"), output)
        found.setdefault(str(step), []).append(output)
    model: dict[str, Any] = {}
    for step in wanted:
        outputs = found.get(step)
        if not outputs:
            continue
        if step == "rerank":
            model[step] = merge_pins(outputs)
        elif step in SEQUENCES:
            model[step] = outputs if len(outputs) > 1 else outputs[0]
        else:
            model[step] = outputs[-1]
    return CaseReplay(case.id, prune(model), [s for s in wanted if s in model])


def merge_pins(calls: Sequence[Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """The pins of every rerank call in a turn (one per sub-query): the first score a candidate
    got stands."""
    seen: set[tuple[str, Any]] = set()
    merged: list[dict[str, Any]] = []
    for pins in calls:
        for pin in pins:
            key = (pin["match"], pin.get("state"))
            if key not in seen:
                seen.add(key)
                merged.append(dict(pin))
    return merged


def rerank_pins(context: Any, output: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A recorded rerank (scores by candidate label) as the replay's pins: a piece of the
    candidate's text to match, its score, and its state where the state matters."""
    labels: Mapping[str, Mapping[str, Any]] = context if isinstance(context, Mapping) else {}
    pins: list[dict[str, Any]] = []
    for row in output.get("scores", []):
        candidate = labels.get(str(row.get("id", "")).strip().lower())
        if candidate is None:
            continue
        pin: dict[str, Any] = {"match": candidate["text"], "score": row["score"]}
        if candidate.get("state"):
            pin["state"] = candidate["state"]
        pins.append(pin)
    return pins


# Fields whose presence is the information: ``reminder: {lead: null}`` is a reminder at the
# default lead, and pruning it to nothing would drop the reminder.
KEEP_WHEN_PRESENT = frozenset({"reminder"})


def _says_nothing(value: Any) -> bool:
    # ``0`` and ``0.0`` say something (a score of zero); only null, false, "" and empty do not.
    return value is None or value is False or value in ("", [], {})


def prune(value: Any) -> Any:
    """A recorded output without the fields that say nothing (null, empty, false), so a case
    file stays readable; the replay loader puts the defaults back."""
    if isinstance(value, Mapping):
        out = {k: prune(v) if v is not None else v for k, v in value.items()}
        return {
            k: v
            for k, v in out.items()
            if not _says_nothing(v) or (k in KEEP_WHEN_PRESENT and v == {})
        }
    if isinstance(value, list):
        return [prune(v) for v in value]
    return value


def current_model(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    model = raw.get("model")
    return prune(dict(model)) if isinstance(model, Mapping) else {}


def render_model(model: Mapping[str, Any]) -> str:
    body = yaml.safe_dump(
        dict(model), sort_keys=False, allow_unicode=True, width=100, default_flow_style=False
    )
    return "model:\n" + "".join(f"  {line}\n" if line else "\n" for line in body.splitlines())


_KEY_LINE = re.compile(r"^[A-Za-z_#]")


def rewrite_model(path: Path, model: Mapping[str, Any]) -> None:
    """Replace the ``model:`` block of a case file, and nothing else."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.startswith("model:")), None)
    block = render_model(model)
    if start is None:
        raise ValueError(f"{path.name} has no model: block to replace")
    end = start + 1
    while end < len(lines) and not _KEY_LINE.match(lines[end]):
        end += 1
    path.write_text("".join(lines[:start]) + block + "".join(lines[end:]), encoding="utf-8")


@dataclass(frozen=True, slots=True)
class Change:
    case_id: str
    path: Path
    steps: list[str]  # steps whose recorded output differs from the file's
    diff: str
    model: dict[str, Any]

    @property
    def changed(self) -> bool:
        return bool(self.steps)


def compare(
    run: RunRecord, suite: str, cases_dir: Path, only: Iterable[str] | None = None
) -> list[Change]:
    """For every case in the run that has a file: what recording would change."""
    wanted = set(only) if only is not None else None
    changes: list[Change] = []
    for case in run.cases:
        if wanted is not None and case.id not in wanted:
            continue
        path = cases_dir / f"{case.id}.yaml"
        if not path.exists():
            continue
        replay = recorded_model(case, suite)
        if not replay.model:
            continue
        before = current_model(path)
        # Keep what the file has for any step the run did not record (e.g. enrich, not called).
        merged = {**before, **replay.model}
        differing = [step for step in STEPS[suite] if before.get(step) != merged.get(step)]
        diff = "".join(
            difflib.unified_diff(
                render_model(before).splitlines(keepends=True),
                render_model(merged).splitlines(keepends=True),
                "current",
                "recorded",
                n=1,
            )
        )
        changes.append(Change(case.id, path, differing, diff, merged))
    return changes


def summary(changes: Sequence[Change], *, show_diff: bool = False, limit: int = 60) -> str:
    lines = []
    for c in changes:
        lines.append(f"{c.case_id}: " + (", ".join(c.steps) if c.steps else "unchanged"))
        if show_diff and c.diff:
            body = c.diff.splitlines()
            lines.extend("    " + row for row in body[:limit])
            if len(body) > limit:
                lines.append(f"    … {len(body) - limit} more lines")
    changed = sum(1 for c in changes if c.changed)
    lines.append(f"{changed} of {len(changes)} cases differ from their replay files")
    return "\n".join(lines)

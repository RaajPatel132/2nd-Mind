"""The offline brain for the ``correct`` step in fake-provider mode: replays the correction a
case recorded, else says honestly that it can't act offline."""

from collections.abc import Mapping
from typing import Any

from secondmind.providers import AdapterRequest, Responder

_CHANGES = dict.fromkeys(
    ("kind", "subtype", "category", "tags", "format", "layer", "state", "date_expression",
     "date_clock"),
)  # fmt: skip
_NONE: dict[str, Any] = {
    "type": "none",
    "target": {"source": "none", "ref": None, "query": None},
    "changes": None,
    "new_text": None,
    "relation": None,
    "match": None,
    "category": None,
    "rule_note": None,
    "assumption": None,
    "reason": "The offline fake can't work out corrections; undo that turn from its glass box.",
}


def complete_correction(data: Mapping[str, Any]) -> dict[str, Any]:
    """Fill what a recorded correction leaves out, so case files stay short."""
    out = {**_NONE, "reason": "", **data}
    out["target"] = {"source": "none", "ref": None, "query": None, **(data.get("target") or {})}
    if isinstance(data.get("changes"), Mapping):
        out["changes"] = {**_CHANGES, **data["changes"]}
    return out


def correction_responders(
    replay: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Responder]:
    replay = replay or {}

    def correct(request: AdapterRequest) -> dict[str, Any]:
        users = [m.content for m in request.messages if m.role == "user"]
        key = " ".join((users[-1] if users else "").lower().split())
        recorded = replay.get(key, {}).get("correct")
        return complete_correction(recorded) if isinstance(recorded, Mapping) else dict(_NONE)

    return {"correct": correct}

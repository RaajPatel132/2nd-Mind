"""Goldens pin what matters, not one wording of it: a ``|`` marks answers a person would accept
equally (a name, a role, a shape, a reply, a count)."""

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from secondmind.evals.ingest import _alternatives
from secondmind.evals.recall import RecallCase, failures


def test_alternatives_split_names_and_roles_or_kinds() -> None:
    assert _alternatives("MK bag|MK:thing") == ({"mk bag", "mk"}, {"thing"})
    assert _alternatives("Nisha:with|for") == ({"nisha"}, {"with", "for"})
    assert _alternatives("wife") == ({"wife"}, set())


def _case(**fields: Any) -> RecallCase:
    base: dict[str, Any] = {
        "id": "x",
        "title": "x",
        "question": "q",
        "now": None,
        "timezone": "UTC",
        "shape": (),
        "gold": (),
        "forbidden": (),
        "aggregate": (),
        "must_abstain": False,
        "reply_contains": (),
        "fired": (),
        "not_fired": (),
        "fresh": False,
        "setup": (),
        "model": {},
        "path": Path("x.yaml"),
    }
    return RecallCase(**(base | fields))


@dataclass
class _Run:
    """The parts of a recall run that ``failures`` reads."""

    case: RecallCase
    shapes: list[str] = field(default_factory=list)
    aggregate: float | None = None
    reply: str = ""
    ranked: list[str] = field(default_factory=list)
    abstained: bool = False
    fired: list[str] = field(default_factory=list)
    turn: Any = field(
        default_factory=lambda: SimpleNamespace(status=SimpleNamespace(value="completed"))
    )


def test_a_shape_may_be_either_of_two() -> None:
    case = _case(shape=("entity|exact",))
    assert failures(_Run(case, shapes=["exact"])) == []  # type: ignore[arg-type]
    assert failures(_Run(case, shapes=["entity"])) == []  # type: ignore[arg-type]
    assert failures(_Run(case, shapes=["list"])) != []  # type: ignore[arg-type]
    assert case.primary_shape == "entity"


def test_a_count_may_be_either_of_two() -> None:
    case = _case(aggregate=(6.0, 7.0))
    assert failures(_Run(case, aggregate=7.0)) == []  # type: ignore[arg-type]
    assert failures(_Run(case, aggregate=6.0)) == []  # type: ignore[arg-type]
    (problem,) = failures(_Run(case, aggregate=5.0))  # type: ignore[arg-type]
    assert "6.0 or 7.0" in problem


def test_a_reply_may_say_it_either_way() -> None:
    case = _case(reply_contains=("File them as runs?|File it as a run?",))
    assert failures(_Run(case, reply="One more looks like a run. File it as a run?")) == []  # type: ignore[arg-type]
    assert failures(_Run(case, reply="Nothing to file.")) != []  # type: ignore[arg-type]

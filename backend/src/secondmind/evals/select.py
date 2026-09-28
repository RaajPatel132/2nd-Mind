"""Which cases a run covers (R.1): re-run what a fix touched, never "everything".

* ``--cases a,b`` takes ids (a unique prefix such as ``07`` is enough), tags (a recall shape,
  ``abstain``, ``multi``, ``fresh``, or tags a case file lists) and named subsets (``@probe``,
  from ``evals/subsets.yaml``);
* ``--sample N --stratify shape`` takes a deterministic sample of N that covers every stratum
  before taking a second case from any;
* ``--only failed --from RUN`` takes the cases that failed in an earlier run.
"""

import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from secondmind.config import DEFAULT_RESOURCES_DIR

SUBSETS_FILE = DEFAULT_RESOURCES_DIR / "evals" / "subsets.yaml"
SAMPLE_SEED = 39


class SelectionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Selection:
    cases: tuple[str, ...] = ()
    sample: int | None = None
    stratify: str | None = None
    only_failed: tuple[str, ...] | None = None  # ids that failed in the --from run

    @property
    def subsets(self) -> tuple[str, ...]:
        return tuple(t[1:] for t in self.cases if t.startswith("@"))

    def describe(self) -> dict[str, object]:
        out: dict[str, object] = {}
        if self.cases:
            out["cases"] = list(self.cases)
        if self.sample is not None:
            out["sample"] = self.sample
            out["stratify"] = self.stratify
        if self.only_failed is not None:
            out["only_failed"] = list(self.only_failed)
        return out

    @property
    def everything(self) -> bool:
        return not self.cases and self.sample is None and self.only_failed is None


def read_subsets(suite: str, path: Path = SUBSETS_FILE) -> dict[str, list[str]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        str(name): [str(i) for i in (spec.get(suite) or [])]
        for name, spec in (raw.get("subsets") or {}).items()
    }


def parse_tokens(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(t.strip() for v in values for t in v.replace(",", " ").split() if t.strip())


def select[C](
    cases: Sequence[C],
    selection: Selection,
    *,
    suite: str,
    id_of: Callable[[C], str],
    tags_of: Callable[[C], Sequence[str]],
    stratum_of: Callable[[C], str] | None = None,
    subsets: dict[str, list[str]] | None = None,
) -> list[C]:
    """The cases ``selection`` names, in their file order."""
    chosen = list(cases)
    if selection.only_failed is not None:
        failed = set(selection.only_failed)
        chosen = [c for c in chosen if id_of(c) in failed]
    if selection.cases:
        named = subsets if subsets is not None else read_subsets(suite)
        wanted: set[str] = set()
        for token in selection.cases:
            wanted |= _match(token, cases, id_of, tags_of, named)
        chosen = [c for c in chosen if id_of(c) in wanted]
    if selection.sample is not None:
        chosen = _sample(chosen, selection.sample, stratum_of if selection.stratify else None)
    return chosen


def _match[C](
    token: str,
    cases: Sequence[C],
    id_of: Callable[[C], str],
    tags_of: Callable[[C], Sequence[str]],
    subsets: dict[str, list[str]],
) -> set[str]:
    if token.startswith("@"):
        name = token[1:]
        if name not in subsets:
            raise SelectionError(f"no subset {name!r}; known: {', '.join(sorted(subsets))}")
        out: set[str] = set()
        for item in subsets[name]:
            out |= _match(item, cases, id_of, tags_of, subsets)
        return out
    ids = [id_of(c) for c in cases]
    if token in ids:
        return {token}
    prefixed = [i for i in ids if i.startswith(token)]
    if len(prefixed) == 1:
        return set(prefixed)
    tagged = {id_of(c) for c in cases if token in tags_of(c)}
    if tagged:
        return tagged
    if prefixed:
        raise SelectionError(f"{token!r} matches several cases: {prefixed}")
    raise SelectionError(f"{token!r} is not a case id, tag or subset")


def _sample[C](cases: list[C], n: int, stratum_of: Callable[[C], str] | None) -> list[C]:
    if n >= len(cases):
        return cases
    rng = random.Random(SAMPLE_SEED)  # noqa: S311 - a reproducible sample, not a secret
    if stratum_of is None:
        picked = set(rng.sample(range(len(cases)), n))
        return [c for i, c in enumerate(cases) if i in picked]
    groups: dict[str, list[int]] = defaultdict(list)
    for index, case in enumerate(cases):
        groups[stratum_of(case)].append(index)
    for members in groups.values():
        rng.shuffle(members)
    order = sorted(groups)
    rng.shuffle(order)
    picked_idx: list[int] = []
    while len(picked_idx) < n:
        for stratum in order:
            if groups[stratum] and len(picked_idx) < n:
                picked_idx.append(groups[stratum].pop())
    keep = set(picked_idx)
    return [c for i, c in enumerate(cases) if i in keep]

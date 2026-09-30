"""Lint rules over a persona seed (S4.10): it is shown to strangers and moved in time, so its text
has to stay true on any date and stay clear of money talk. A finding is a plain sentence naming the
item; an empty list means the seed is clean."""

import re

from secondmind.core import Kind
from secondmind.persona.spec import ItemSpec, WorkspaceSpec

# Nothing about payments, refunds or billing disputes (CLAUDE.md, FR-13.3).
MONEY = re.compile(
    r"\b(pay|pays|paid|paying|payment|payments|refund\w*|bill|bills|billed|billing|invoice\w*|"
    r"charge[ds]?|chargeback|credit card|debit|transaction\w*|receipt\w*|salary|wage\w*|"
    r"price[ds]?|prices|costs?)\b",
    re.IGNORECASE,
)
WEEKDAY_NAMES = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b", re.IGNORECASE
)
MONTH_NAMES = re.compile(
    r"\b(january|february|march|april|june|july|august|september|october|november|december)\b",
    re.IGNORECASE,
)
LITERAL_DATE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}(st|nd|rd|th)?\s+(of\s+)?"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
    re.IGNORECASE,
)
RELATIVE_DAY = re.compile(
    r"\b(yesterday|tomorrow|tonight|last (week|month|year|night|weekend)|"
    r"next (week|month|year|weekend)|this (week|month|year|weekend|morning|evening)|"
    r"a few (days|weeks|months) (ago|from now)|the day after|the day before)\b",
    re.IGNORECASE,
)

ALL_KINDS = frozenset(Kind)
INPUT_KINDS = ("link-full", "link-partial", "video", "image", "pdf")


def _texts(item: ItemSpec) -> list[str]:
    out = [item.title, item.text]
    if item.summary:
        out.append(item.summary)
    if item.source:
        out.extend(item.source.passages)
        if item.source.extracted:
            out.append(item.source.extracted)
    return out


def recurs(item: ItemSpec) -> bool:
    """Only a recurring item may name a weekday: its clock repeats on it."""
    return item.rrule is not None


def input_kind(item: ItemSpec) -> str | None:
    src = item.source
    if src is None:
        return None
    if src.kind == "link":
        return "link-full" if src.status == "full" else "link-partial"
    return src.kind


def lint_spec(spec: WorkspaceSpec) -> list[str]:
    """Every finding over ``spec`` (series expanded): money words, weekday names, months,
    literal dates and relative days in text that must stay true when the seed is moved."""
    spec = spec.expanded()
    findings: list[str] = []
    for item in spec.items:
        for text in _texts(item):
            if (hit := MONEY.search(text)) is not None:
                findings.append(f"{item.key}: talks about money ({hit.group(0)!r})")
            if LITERAL_DATE.search(text):
                findings.append(f"{item.key}: holds a literal date")
            if MONTH_NAMES.search(text):
                findings.append(f"{item.key}: names a month")
            if (rel := RELATIVE_DAY.search(text)) is not None:
                findings.append(f"{item.key}: uses a relative day ({rel.group(0)!r})")
            if not recurs(item) and WEEKDAY_NAMES.search(text):
                findings.append(f"{item.key}: names a weekday but doesn't recur")
    for turn in spec.turns:
        for text in (turn.user, turn.assistant):
            if (hit := MONEY.search(text)) is not None:
                findings.append(f"turn {turn.key}: talks about money ({hit.group(0)!r})")
            if LITERAL_DATE.search(text) or WEEKDAY_NAMES.search(text) or MONTH_NAMES.search(text):
                findings.append(f"turn {turn.key}: holds a date or a weekday")
    return findings


def coverage(spec: WorkspaceSpec) -> tuple[set[Kind], set[str]]:
    """The item kinds and the input kinds (link-full, link-partial, video, image, pdf) present."""
    spec = spec.expanded()
    kinds = {i.kind for i in spec.items}
    inputs = {k for i in spec.items if (k := input_kind(i)) is not None}
    return kinds, inputs


def missing(spec: WorkspaceSpec) -> list[str]:
    kinds, inputs = coverage(spec)
    out = [f"no item of kind {k.value}" for k in sorted(ALL_KINDS - kinds, key=lambda k: k.value)]
    out += [f"no {k} input" for k in INPUT_KINDS if k not in inputs]
    return out

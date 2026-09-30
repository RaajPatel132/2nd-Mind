"""The shape of a seeded workspace (S3.1, S4.10): entities, items, links, relations and past turns,
written in YAML and loaded through ``MemoryWriter`` by :func:`secondmind.persona.seed_workspace`.

Both the recall fixture (``evals/fixtures/recall.yaml``) and the Aditi Rao persona
(``seeds/persona/aditi.yaml``) are a :class:`WorkspaceSpec`. The persona adds two things: times
written as days from its anchor (no calendar dates in it), and ``series``, which expand a template
into many items so the runs, gym visits and episodes stay a few lines each.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from secondmind.core import (
    EntityKind,
    EntityRole,
    Kind,
    LinkType,
    ResourceFormat,
    Sensitivity,
    TimePrecision,
    TriggerOn,
)


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EntitySpec(_Spec):
    key: str
    kind: EntityKind
    name: str
    aliases: list[str] = []
    labels: list[str] = []
    is_key: bool = False
    attributes: dict[str, str] = {}


class RoleSpec(_Spec):
    entity: str
    role: EntityRole


class TimeSpec(_Spec):
    at: str
    precision: TimePrecision = TimePrecision.DAY


class OccurredSpec(_Spec):
    start: str
    end: str | None = None
    precision: TimePrecision = TimePrecision.DAY


class TriggerSpec(_Spec):
    on: TriggerOn
    entity: str | None = None
    cue: str | None = None
    fires_at: str | None = None
    lead_minutes: int | None = None


class SourceSpec(_Spec):
    """Where a resource came from, written as if it had been saved through the app (S4.10).

    ``link`` and ``video`` become a ``link_sources`` row with the page's passages as chunk keys;
    ``image`` and ``pdf`` are stored already extracted (content derived, no original file until
    S9), their extracted text being the item's raw content."""

    kind: Literal["link", "video", "image", "pdf"]
    url: str | None = None
    status: Literal["full", "partial"] = "full"
    reason: str | None = None
    site: str | None = None
    author: str | None = None
    published: str | None = None
    channel: str | None = None
    duration_s: int | None = None
    words: int | None = None
    passages: list[str] = []
    extracted: str | None = None

    @model_validator(mode="after")
    def _complete(self) -> "SourceSpec":
        if self.kind in ("link", "video") and not self.url:
            raise ValueError(f"a {self.kind} needs a url")
        if self.kind in ("image", "pdf") and not self.extracted:
            raise ValueError(f"a {self.kind} needs the text extracted from it")
        if self.status == "partial" and not self.reason:
            raise ValueError("a partial link says why")
        return self


class ItemSpec(_Spec):
    key: str
    kind: Kind
    subtype: str | None = None
    format: ResourceFormat | None = None
    state: str | None = None
    title: str
    text: str
    summary: str | None = None
    subject: str | None = None
    predicate: str | None = None
    value: str | dict[str, Any] | None = None
    attributes: dict[str, str] = {}
    entities: list[RoleSpec] = []
    category: str | None = None
    tags: list[str] = []
    occurred: OccurredSpec | None = None
    rrule: str | None = None
    due: TimeSpec | None = None
    valid_from: TimeSpec | None = None
    mentioned: str
    sensitivity: Sensitivity = Sensitivity.NORMAL
    sentiment: int | None = None
    importance: int | None = None
    in_core: bool = False
    triggers: list[TriggerSpec] = []
    source: SourceSpec | None = None


class LinkSpec(_Spec):
    type: LinkType
    src: str
    dst: str
    valid_to: str | None = None


class RelationSpec(_Spec):
    src: str
    relation: str
    dst: str
    evidence: str | None = None


class TurnSpec(_Spec):
    key: str
    at: str
    user: str
    assistant: str


class SeriesSpec(_Spec):
    """A template for many items. ``{n}`` counts from 1, ``{day}`` is the item's offset in days from
    the anchor (``from_days`` plus ``every_days`` for each step), and every key of ``vary`` cycles
    through its values. A string that is exactly one placeholder keeps the value's own type."""

    count: int = Field(gt=0)
    every_days: int = -1
    from_days: int = 0
    vary: dict[str, list[Any]] = {}
    item: dict[str, Any]


class WorkspaceSpec(_Spec):
    entities: list[EntitySpec] = []
    relations: list[RelationSpec] = []
    items: list[ItemSpec] = []
    links: list[LinkSpec] = []
    turns: list[TurnSpec] = []
    series: list[SeriesSpec] = []

    def expanded(self) -> "WorkspaceSpec":
        """The spec with every series written out as items (none left in ``series``)."""
        if not self.series:
            return self
        made: list[ItemSpec] = []
        for series in self.series:
            made.extend(_expand(series))
        items = [*self.items, *made]
        keys = [i.key for i in items]
        duplicate = next((k for k in keys if keys.count(k) > 1), None)
        if duplicate is not None:
            raise ValueError(f"two items have the key {duplicate!r}")
        return self.model_copy(update={"items": items, "series": []})


_WHOLE = re.compile(r"^\{(\w+)\}$")


def _fill(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        whole = _WHOLE.match(value)
        if whole and whole.group(1) in context:
            return context[whole.group(1)]
        return value.format_map(context)
    if isinstance(value, dict):
        return {k: _fill(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, context) for v in value]
    return value


def _expand(series: SeriesSpec) -> list[ItemSpec]:
    out: list[ItemSpec] = []
    for i in range(series.count):
        context: dict[str, Any] = {"n": i + 1, "day": series.from_days + i * series.every_days}
        context |= {name: values[i % len(values)] for name, values in series.vary.items()}
        out.append(ItemSpec.model_validate(_fill(series.item, context)))
    return out

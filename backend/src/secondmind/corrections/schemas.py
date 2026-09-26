"""What ``correct@1`` returns: the target and the change (S3.12). Code checks and applies it."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CorrectionTarget(_Out):
    source: Literal["previous_turn", "search", "none"]
    ref: str | None
    query: str | None


class CorrectionChanges(_Out):
    kind: str | None
    subtype: str | None
    category: str | None
    tags: list[str] | None
    format: str | None
    layer: Literal["core", "quick", "archive"] | None
    state: str | None
    date_expression: str | None
    date_clock: Literal["occurred", "due", "valid"] | None


class RelationFix(_Out):
    subject: str
    object: str
    wrong: str
    right: str


class CorrectionOut(_Out):
    type: Literal["reclassify", "wrong_value", "forget", "bulk", "none"]
    target: CorrectionTarget
    changes: CorrectionChanges | None
    new_text: str | None
    relation: RelationFix | None
    match: str | None
    category: str | None
    rule_note: str | None
    assumption: str | None
    reason: str

"""S2.3: every write-policy rule, table-driven, with an allowed, a held and a blocked case.

Trust is tested with ops built with ``trust=content_derived`` directly: there is nothing
content-derived to ingest until S4.
"""

from dataclasses import replace

import pytest

from secondmind.core import Kind, Modality, PolicyDecision, Sensitivity, Trust, WriteOp
from secondmind.policy import OpFacts, PolicyContext, evaluate

A, H, B = PolicyDecision.ALLOWED, PolicyDecision.HELD, PolicyDecision.BLOCKED

CREATE_FACT = OpFacts(op=WriteOp.CREATE, kind=Kind.FACT)
USER = PolicyContext()

CASES: list[tuple[str, OpFacts, PolicyContext, PolicyDecision, str]] = [
    # P-SECRET-1
    ("secret: normal fact allowed", CREATE_FACT, USER, A, "P-DEFAULT"),
    (
        "secret: personal fact allowed",
        replace(CREATE_FACT, sensitivity=Sensitivity.PERSONAL),
        USER,
        A,
        "P-DEFAULT",
    ),
    (
        "secret: secret blocked even when confirmed",
        replace(CREATE_FACT, sensitivity=Sensitivity.SECRET),
        PolicyContext(confirmed=True),
        B,
        "P-SECRET-1",
    ),
    # P-TRUST-1
    (
        "trust: content-derived resource allowed",
        OpFacts(
            op=WriteOp.CREATE, kind=Kind.RESOURCE, trust=Trust.CONTENT_DERIVED, origin="content"
        ),
        USER,
        A,
        "P-DEFAULT",
    ),
    (
        "trust: content-derived core write held",
        OpFacts(
            op=WriteOp.UPDATE,
            kind=Kind.FACT,
            trust=Trust.CONTENT_DERIVED,
            origin="content",
            core_write=True,
        ),
        USER,
        H,
        "P-TRUST-1",
    ),
    (
        "trust: content-derived rule blocked",
        OpFacts(op=WriteOp.CREATE, kind=Kind.RULE, trust=Trust.CONTENT_DERIVED, origin="content"),
        USER,
        B,
        "P-TRUST-1",
    ),
    (
        "trust: content-derived trigger blocked",
        OpFacts(
            op=WriteOp.CREATE,
            kind=Kind.TASK,
            trust=Trust.CONTENT_DERIVED,
            origin="content",
            creates_trigger=True,
        ),
        USER,
        B,
        "P-TRUST-1",
    ),
    (
        "trust: content-derived delete blocked",
        OpFacts(
            op=WriteOp.DELETE,
            kind=Kind.NOTE,
            trust=Trust.CONTENT_DERIVED,
            origin="content",
            edits_existing=True,
        ),
        USER,
        B,
        "P-TRUST-1",
    ),
    # P-CORE-1
    (
        "core: core write from the user's message allowed",
        replace(CREATE_FACT, op=WriteOp.UPDATE, core_write=True),
        USER,
        A,
        "P-DEFAULT",
    ),
    (
        "core: core write from a system turn held",
        replace(CREATE_FACT, op=WriteOp.UPDATE, core_write=True, origin="system"),
        PolicyContext(turn_kind="system"),
        H,
        "P-CORE-1",
    ),
    (
        "core: held core write allowed once confirmed",
        replace(CREATE_FACT, op=WriteOp.UPDATE, core_write=True, origin="system"),
        PolicyContext(turn_kind="confirm", confirmed=True),
        A,
        "P-DEFAULT",
    ),
    # P-SENS-1
    (
        "sens: sensitive archive write allowed",
        replace(CREATE_FACT, sensitivity=Sensitivity.SENSITIVE),
        USER,
        A,
        "P-DEFAULT",
    ),
    (
        "sens: sensitive core write held even when user-stated",
        replace(CREATE_FACT, op=WriteOp.UPDATE, core_write=True, sensitivity=Sensitivity.SENSITIVE),
        USER,
        H,
        "P-SENS-1",
    ),
    (
        "sens: sensitive core write allowed once confirmed",
        replace(CREATE_FACT, op=WriteOp.UPDATE, core_write=True, sensitivity=Sensitivity.SENSITIVE),
        PolicyContext(confirmed=True),
        A,
        "P-DEFAULT",
    ),
    # P-MOD-1
    (
        "mod: hypothetical note allowed",
        OpFacts(op=WriteOp.CREATE, kind=Kind.NOTE, modality=Modality.HYPOTHETICAL),
        USER,
        A,
        "P-DEFAULT",
    ),
    (
        "mod: reported fact allowed",
        replace(CREATE_FACT, modality=Modality.REPORTED),
        USER,
        A,
        "P-DEFAULT",
    ),
    (
        "mod: hypothetical fact blocked",
        replace(CREATE_FACT, modality=Modality.HYPOTHETICAL),
        USER,
        B,
        "P-MOD-1",
    ),
    (
        "mod: hypothetical core write blocked",
        OpFacts(op=WriteOp.UPDATE, kind=Kind.NOTE, modality=Modality.HYPOTHETICAL, core_write=True),
        USER,
        B,
        "P-MOD-1",
    ),
    # P-INFER-1
    (
        "infer: a user's fact at full confidence allowed",
        CREATE_FACT,
        PolicyContext(turn_kind="system"),
        A,
        "P-DEFAULT",
    ),
    (
        "infer: pattern held",
        OpFacts(op=WriteOp.CREATE, kind=Kind.PATTERN, origin="system", confidence=0.7),
        PolicyContext(turn_kind="system"),
        H,
        "P-INFER-1",
    ),
    (
        "infer: low confidence from a system turn held",
        replace(CREATE_FACT, confidence=0.6, origin="system"),
        PolicyContext(turn_kind="system"),
        H,
        "P-INFER-1",
    ),
    (
        "infer: a hypothetical inferred fact is blocked, not held",
        replace(CREATE_FACT, confidence=0.6, origin="system", modality=Modality.HYPOTHETICAL),
        PolicyContext(turn_kind="system"),
        B,
        "P-MOD-1",
    ),
    # P-BULK-1
    (
        "bulk: a few edits allowed",
        OpFacts(op=WriteOp.UPDATE, kind=Kind.FACT, edits_existing=True),
        PolicyContext(edited_items=3, bulk_threshold=5),
        A,
        "P-DEFAULT",
    ),
    (
        "bulk: more edits than the threshold held",
        OpFacts(op=WriteOp.UPDATE, kind=Kind.FACT, edits_existing=True),
        PolicyContext(edited_items=6, bulk_threshold=5),
        H,
        "P-BULK-1",
    ),
    (
        "bulk: a direct delete held",
        OpFacts(op=WriteOp.DELETE, kind=Kind.NOTE, edits_existing=True),
        PolicyContext(edited_items=1),
        H,
        "P-BULK-1",
    ),
    (
        "bulk: a small undo's delete allowed",
        OpFacts(op=WriteOp.DELETE, kind=Kind.NOTE, edits_existing=True, origin="undo"),
        PolicyContext(turn_kind="undo", edited_items=2),
        A,
        "P-DEFAULT",
    ),
    (
        "bulk: a large undo held",
        OpFacts(op=WriteOp.DELETE, kind=Kind.NOTE, edits_existing=True, origin="undo"),
        PolicyContext(turn_kind="undo", edited_items=9, bulk_threshold=5),
        H,
        "P-BULK-1",
    ),
    (
        "bulk: a secret bulk op is blocked, not held",
        OpFacts(
            op=WriteOp.UPDATE,
            kind=Kind.FACT,
            edits_existing=True,
            sensitivity=Sensitivity.SECRET,
        ),
        PolicyContext(edited_items=9),
        B,
        "P-SECRET-1",
    ),
]


@pytest.mark.parametrize(
    ("name", "op", "ctx", "decision", "rule"), CASES, ids=[c[0] for c in CASES]
)
def test_rule_table(
    name: str, op: OpFacts, ctx: PolicyContext, decision: PolicyDecision, rule: str
) -> None:
    verdict = evaluate(op, ctx)
    assert (verdict.decision, verdict.rule_id) == (decision, rule), verdict.reason
    assert verdict.reason


def test_every_rule_has_an_allowed_a_held_or_blocked_case() -> None:
    from secondmind.policy import RULES  # noqa: PLC0415

    fired = {rule for _, _, _, d, rule in CASES if d is not PolicyDecision.ALLOWED}
    assert {rule_id for rule_id, _ in RULES} <= fired

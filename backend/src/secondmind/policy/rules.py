"""The write policy as a rule table in code (§7.4), not a prompt.

``evaluate(op, context)`` runs every rule. Each rule is a plain function that returns a verdict
or ``None`` (not applicable). The most severe verdict wins (blocked > held > allowed); ties go to
the rule listed first. Rule ids are stable: they appear in the write log, the ``policy`` event
and the Tool calls panel.
"""

from collections.abc import Callable
from dataclasses import dataclass

from secondmind.core import (
    Kind,
    Modality,
    PolicyDecision,
    PolicyVerdict,
    Sensitivity,
    Trust,
    WriteOp,
)

# Op origins that are the user's own action (their message, an undo, a confirmation, an edit
# in the glass box).
USER_ORIGINS = frozenset({"user_message", "undo", "confirm", "ui_edit"})

# Ops that change an item or entity that already exists.
EDIT_OPS = frozenset(
    {
        WriteOp.UPDATE,
        WriteOp.SUPERSEDE,
        WriteOp.FULFIL,
        WriteOp.SET_STATE,
        WriteOp.DELETE,
        WriteOp.RESTORE,
    }
)

# What content-derived material may still do: store itself and what it mentions.
CONTENT_ALLOWED_OPS = frozenset({WriteOp.CREATE, WriteOp.LINK, WriteOp.UPSERT_ENTITY})


@dataclass(frozen=True, slots=True)
class OpFacts:
    """What the policy needs to know about one proposed op."""

    op: WriteOp
    target: str = "item"
    kind: Kind | None = None
    trust: Trust = Trust.USER_STATED
    origin: str = "user_message"
    sensitivity: Sensitivity = Sensitivity.NORMAL
    modality: Modality = Modality.ASSERTED
    confidence: float = 1.0
    core_write: bool = False
    creates_trigger: bool = False
    edits_existing: bool = False
    # Only moves an item in or out of the quick layer: housekeeping, not a change to a memory.
    layer_only: bool = False


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """The turn around the op."""

    turn_kind: str = "user"
    confirmed: bool = False
    edited_items: int = 0
    bulk_threshold: int = 5


Rule = Callable[[OpFacts, PolicyContext], PolicyVerdict | None]


def _held(rule_id: str, reason: str) -> PolicyVerdict:
    return PolicyVerdict(decision=PolicyDecision.HELD, rule_id=rule_id, reason=reason)


def _blocked(rule_id: str, reason: str) -> PolicyVerdict:
    return PolicyVerdict(decision=PolicyDecision.BLOCKED, rule_id=rule_id, reason=reason)


def p_secret_1(op: OpFacts, ctx: PolicyContext) -> PolicyVerdict | None:
    """Secrets (passwords, PINs, one-time codes, API keys) are never stored (ADR-0021)."""
    if op.sensitivity is Sensitivity.SECRET:
        return _blocked("P-SECRET-1", "looks like a secret (password, PIN, code or key)")
    return None


def p_trust_1(op: OpFacts, ctx: PolicyContext) -> PolicyVerdict | None:
    """Content-derived material is data, never instructions (FR-4.3)."""
    if op.trust is not Trust.CONTENT_DERIVED:
        return None
    if op.core_write:
        return _held("P-TRUST-1", "a core write proposed from content, not from you")
    if op.kind is Kind.RULE or op.creates_trigger or op.op not in CONTENT_ALLOWED_OPS:
        return _blocked("P-TRUST-1", "content can't change rules, set triggers or edit memory")
    return None


def p_core_1(op: OpFacts, ctx: PolicyContext) -> PolicyVerdict | None:
    """A core write is allowed only when it comes from the user's own message (FR-4.4)."""
    if op.core_write and not ctx.confirmed and op.origin not in USER_ORIGINS:
        return _held("P-CORE-1", "core writes need your own message or confirmation")
    return None


def p_sens_1(op: OpFacts, ctx: PolicyContext) -> PolicyVerdict | None:
    """Sensitive content never reaches core without confirmation, even when user-stated."""
    if op.core_write and op.sensitivity is Sensitivity.SENSITIVE and not ctx.confirmed:
        return _held("P-SENS-1", "sensitive, so it goes to core only if you confirm")
    return None


def p_mod_1(op: OpFacts, ctx: PolicyContext) -> PolicyVerdict | None:
    """A hypothetical is never stored as a fact or preference, or in core."""
    if op.modality is not Modality.HYPOTHETICAL:
        return None
    as_fact = op.op is WriteOp.CREATE and op.kind in (Kind.FACT, Kind.PREFERENCE)
    if as_fact or op.core_write:
        return _blocked("P-MOD-1", "hypothetical, so not stored as a fact")
    return None


def p_infer_1(op: OpFacts, ctx: PolicyContext) -> PolicyVerdict | None:
    """Inferred memories (patterns, or low confidence from a system turn) wait for the user.
    Quick-layer housekeeping infers nothing, so it isn't held."""
    if (
        ctx.confirmed
        or op.layer_only
        or op.op in (WriteOp.DELETE, WriteOp.UNLINK, WriteOp.UNRELATE)
    ):
        return None
    inferred = op.kind is Kind.PATTERN or (op.confidence < 1 and ctx.turn_kind == "system")
    if inferred and op.target == "item":
        return _held("P-INFER-1", "inferred, so it waits for your confirmation")
    return None


def p_bulk_1(op: OpFacts, ctx: PolicyContext) -> PolicyVerdict | None:
    """Deletes, and edits to more than the threshold of items in one turn, need confirmation
    (FR-4.5). An undo's reversals count as edits: only a large undo is held. Quick-layer
    housekeeping doesn't count."""
    if ctx.confirmed or op.layer_only or not op.edits_existing:
        return None
    if op.op is WriteOp.DELETE and op.origin not in ("undo", "system"):
        return _held("P-BULK-1", "deleting needs your confirmation")
    if ctx.edited_items > ctx.bulk_threshold:
        return _held(
            "P-BULK-1",
            f"changes {ctx.edited_items} items (more than {ctx.bulk_threshold}) in one turn",
        )
    return None


def p_default(op: OpFacts, ctx: PolicyContext) -> PolicyVerdict:
    return PolicyVerdict(decision=PolicyDecision.ALLOWED, rule_id="P-DEFAULT", reason="allowed")


RULES: tuple[tuple[str, Rule], ...] = (
    ("P-SECRET-1", p_secret_1),
    ("P-TRUST-1", p_trust_1),
    ("P-MOD-1", p_mod_1),
    ("P-INFER-1", p_infer_1),
    ("P-SENS-1", p_sens_1),
    ("P-CORE-1", p_core_1),
    ("P-BULK-1", p_bulk_1),
)

_SEVERITY = {PolicyDecision.ALLOWED: 0, PolicyDecision.HELD: 1, PolicyDecision.BLOCKED: 2}


def evaluate(op: OpFacts, context: PolicyContext) -> PolicyVerdict:
    """The verdict for one proposed op: the most severe rule that applies, else P-DEFAULT."""
    worst: PolicyVerdict | None = None
    for _, rule in RULES:
        verdict = rule(op, context)
        if verdict is not None and (
            worst is None or _SEVERITY[verdict.decision] > _SEVERITY[worst.decision]
        ):
            worst = verdict
    return worst or p_default(op, context)

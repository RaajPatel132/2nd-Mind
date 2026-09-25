"""S2.9: quick-layer expiry is planned in code and applied as a system turn that the policy lets
through: moving items out of quick is housekeeping, not a bulk edit or an inference."""

from datetime import timedelta

from secondmind.core import (
    Kind,
    PolicyDecision,
    TriggerOn,
    TriggerState,
    WriteOp,
    new_id,
)
from secondmind.memory import NewTrigger, SetTriggerState, TriggerContent, UpdateItem
from tests.unit.memory.helpers import NOW, create, item, memory, scope, turn

LATER = NOW + timedelta(days=8)


async def test_expired_entries_leave_quick_or_stay_under_the_next_rule() -> None:
    mem, _ = memory()
    ws = scope()
    stale = create(
        item(
            "Buy stamps",
            Kind.NOTE,
            in_quick=True,
            quick_reason="mentioned in the last 7 days",
            quick_until=NOW + timedelta(days=7),
        )
    )
    dinner = create(
        item(
            "Dinner with Kabir",
            Kind.PLAN,
            occurred_start=NOW + timedelta(days=20),
            in_quick=True,
            quick_reason="mentioned in the last 7 days",
            quick_until=NOW + timedelta(days=7),
        )
    )
    reminded = create(item("Call the plumber", Kind.PLAN, occurred_start=NOW + timedelta(days=2)))
    reminded = reminded.model_copy(
        update={
            "triggers": [
                NewTrigger(
                    trigger_id=new_id(),
                    trigger=TriggerContent(on=TriggerOn.TIME, fires_at=NOW + timedelta(days=1)),
                )
            ]
        }
    )
    writer = mem.writer(ws, turn(ws))
    writer.add(stale, dinner, reminded)
    await writer.commit()

    ops = await mem.expiry_ops(ws, LATER)

    updates = {op.item_id: op.changes for op in ops if isinstance(op, UpdateItem)}
    assert updates[stale.item_id] == {"in_quick": False, "quick_reason": None, "quick_until": None}
    assert updates[dinner.item_id]["in_quick"] is True
    assert updates[dinner.item_id]["quick_reason"] == "plan within 30 days"
    [expire] = [op for op in ops if isinstance(op, SetTriggerState)]
    assert expire.trigger_id == reminded.triggers[0].trigger_id
    assert expire.state is TriggerState.EXPIRED
    assert expire.title == "reminder: Call the plumber"
    assert await mem.expiry_ops(ws, NOW) == []


async def test_a_large_expiry_on_a_system_turn_is_neither_bulk_held_nor_inferred() -> None:
    mem, _ = memory(bulk_threshold=2)
    ws = scope()
    notes = [
        create(
            item(
                f"passing thought {n}",
                Kind.NOTE,
                confidence=0.6,  # would be "inferred" if this were a memory change
                in_quick=True,
                quick_reason="mentioned in the last 7 days",
                quick_until=NOW + timedelta(days=7),
            )
        )
        for n in range(5)
    ]
    first = mem.writer(ws, turn(ws))
    first.add(*notes)
    await first.commit()

    system = turn(ws, kind="system", now=LATER)
    writer = mem.writer(ws, system)
    writer.add(*await mem.expiry_ops(ws, LATER))
    result = await writer.commit()

    log = await mem.reader(ws).write_log(system.turn_id)
    assert [r.op for r in log] == [WriteOp.UPDATE] * 5
    assert {r.decision for r in log} == {PolicyDecision.ALLOWED}
    assert not result.held
    stored = await mem.reader(ws).items([n.item_id for n in notes])
    assert not any(i.in_quick for i in stored)

    # A system turn is undoable like any other.
    await mem.undo(ws, turn(ws, kind="undo", now=LATER), system.turn_id)
    stored = await mem.reader(ws).items([n.item_id for n in notes])
    assert all(i.in_quick for i in stored)

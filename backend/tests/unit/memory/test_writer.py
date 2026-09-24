"""S2.2 / S2.3 / S2.9: the memory writer is the unit of work for a turn."""

import pytest

from secondmind.core import (
    EntityRole,
    Kind,
    Layer,
    MemoryDiffEvent,
    Modality,
    PolicyDecision,
    PolicyEvent,
    Sensitivity,
    TargetType,
    ToolCallEvent,
    WriteOp,
    new_id,
)
from secondmind.memory import SupersedeItem, UpdateItem
from secondmind.memory.adapters import InjectedFaultError
from tests.unit.memory.helpers import (
    NOW,
    Events,
    create,
    item,
    memory,
    person,
    scope,
    sensitive,
    turn,
)


async def test_commit_applies_ops_logs_them_and_emits_a_diff_last() -> None:
    mem, _ = memory()
    ws = scope()
    events = Events()
    nisha = person("Nisha", "sister")
    note = create(item("Nisha likes tulips", Kind.PREFERENCE), (nisha.entity_id, EntityRole.ABOUT))
    writer = mem.writer(ws, turn(ws), emit=events)
    writer.add(nisha, note)

    result = await writer.commit()

    reader = mem.reader(ws)
    stored = await reader.item(note.item_id)
    assert stored is not None
    assert stored.text == "Nisha likes tulips"
    assert (await reader.entity(nisha.entity_id)) is not None
    log = await reader.write_log(writer.turn.turn_id)
    assert [(r.op, r.target_type) for r in log] == [
        (WriteOp.UPSERT_ENTITY, TargetType.ENTITY),
        (WriteOp.CREATE, TargetType.ITEM),
        (WriteOp.LINK, TargetType.ITEM_ENTITY),
    ]
    assert all(r.decision is PolicyDecision.ALLOWED for r in log)
    assert log[1].before is None
    assert log[1].after is not None
    assert log[1].after["text"] == "Nisha likes tulips"
    # policy + tool_call per op, then exactly one diff built from the log
    assert [e.type for e in events.events] == [
        "policy",
        "tool_call",
        "policy",
        "tool_call",
        "memory_diff",
    ]
    diff = events.events[-1]
    assert isinstance(diff, MemoryDiffEvent)
    assert [(e.op, e.title) for e in diff.entries] == [
        ("added", "Nisha"),
        ("added", "Nisha likes tulips"),
    ]
    assert diff.entries[0].entity_id == nisha.entity_id
    assert result.created_items == {note.item_id}
    call = events.of("tool_call")[1]
    assert isinstance(call, ToolCallEvent)
    assert call.tool == "memory.create"
    assert call.policy is not None
    assert call.policy.rule_id == "P-DEFAULT"


async def test_a_failure_halfway_through_a_multi_item_save_persists_nothing() -> None:
    mem, db = memory()
    ws = scope()
    events = Events()
    first = create(item("first thing"))
    second = create(item("second thing"))
    writer = mem.writer(ws, turn(ws), emit=events)
    writer.add(person("Kabir", "partner"), first, second)
    db.faults["insert_item"] = 2  # the second item's insert fails

    with pytest.raises(InjectedFaultError):
        await writer.commit()

    reader = mem.reader(ws)
    assert await reader.items([first.item_id, second.item_id]) == []
    assert [e.name for e in await reader.entities()] == ["me"]
    assert await reader.write_log(writer.turn.turn_id) == []
    assert events.events == []  # nothing claims a write that didn't happen


async def test_a_sensitive_core_write_is_held_and_confirming_applies_it_as_a_new_turn() -> None:
    mem, _ = memory()
    ws = scope()
    content = sensitive(item("I see a therapist on Tuesdays", Kind.FACT))
    new = create(content)
    promote = UpdateItem(item_id=new.item_id, changes={"in_core": True}, title=content.title)
    writer = mem.writer(ws, turn(ws))
    writer.add(new, promote)

    result = await writer.commit()

    held = [e for e in result.diff.entries if e.op == "held"]
    assert len(held) == 1
    assert held[0].rule_id == "P-SENS-1"
    assert held[0].layer is Layer.CORE
    assert held[0].held_write_id is not None
    stored = await mem.reader(ws).item(new.item_id)
    assert stored is not None
    assert not stored.in_core  # saved to the archive, not to core
    pending = await mem.reader(ws).held_writes(status="pending")
    assert [h.id for h in pending] == [held[0].held_write_id]

    confirm = turn(ws, kind="confirm")
    confirmed = await mem.confirm_held(ws, confirm, held[0].held_write_id)

    stored = await mem.reader(ws).item(new.item_id)
    assert stored is not None
    assert stored.in_core
    assert stored.core_confirmed_at == NOW
    assert [e.op for e in confirmed.diff.entries] == ["updated"]
    assert (await mem.reader(ws).held_write(held[0].held_write_id)).status == "confirmed"  # type: ignore[union-attr]
    # Confirmation was its own turn, so undo still works on it.
    await mem.undo(ws, turn(ws, kind="undo"), confirm.turn_id)
    stored = await mem.reader(ws).item(new.item_id)
    assert stored is not None
    assert not stored.in_core


async def test_a_blocked_write_leaves_no_content_in_the_log() -> None:
    mem, _ = memory()
    ws = scope()
    events = Events()
    secret = item("wifi password [redacted]", Kind.FACT, sensitivity=Sensitivity.SECRET)
    op = create(secret)
    writer = mem.writer(ws, turn(ws), emit=events)
    writer.add(op)

    result = await writer.commit()

    assert await mem.reader(ws).item(op.item_id) is None
    log = await mem.reader(ws).write_log(writer.turn.turn_id)
    assert [(r.decision, r.rule_id, r.before, r.after) for r in log] == [
        (PolicyDecision.BLOCKED, "P-SECRET-1", None, None)
    ]
    assert [(e.op, e.rule_id, e.title) for e in result.diff.entries] == [
        ("not_written", "P-SECRET-1", "a secret (not shown)")
    ]
    policy = events.of("policy")[0]
    assert isinstance(policy, PolicyEvent)
    assert policy.verdict.decision is PolicyDecision.BLOCKED
    call = events.of("tool_call")[0]
    assert isinstance(call, ToolCallEvent)
    assert call.arguments == {"kind": "fact", "content": "[redacted]"}


async def test_a_hypothetical_fact_is_not_written() -> None:
    mem, _ = memory()
    ws = scope()
    op = create(item("I will move to Berlin", Kind.FACT, modality=Modality.HYPOTHETICAL))
    writer = mem.writer(ws, turn(ws))
    writer.add(op)

    result = await writer.commit()

    assert await mem.reader(ws).item(op.item_id) is None
    assert [(e.op, e.rule_id) for e in result.diff.entries] == [("not_written", "P-MOD-1")]


async def test_a_core_write_past_the_budget_is_not_made_but_the_item_is_kept() -> None:
    mem, _ = memory(core_token_budget=100)
    ws = scope()
    long_text = "I prefer " + "very " * 80 + "quiet trains"
    new = create(item(long_text, Kind.PREFERENCE))
    writer = mem.writer(ws, turn(ws))
    writer.add(new, UpdateItem(item_id=new.item_id, changes={"in_core": True}, title="pref"))

    result = await writer.commit()

    not_written = [e for e in result.diff.entries if e.op == "not_written"]
    assert [e.rule_id for e in not_written] == ["CORE-BUDGET"]
    assert "core budget full" in not_written[0].reason
    stored = await mem.reader(ws).item(new.item_id)
    assert stored is not None
    assert not stored.in_core


async def test_supersede_keeps_the_old_row_as_history() -> None:
    mem, _ = memory()
    ws = scope()
    old = create(item("I live in Bengaluru", Kind.FACT, predicate="lives_in"))
    first = mem.writer(ws, turn(ws))
    first.add(old)
    await first.commit()

    new = create(item("I live in Pune", Kind.FACT, predicate="lives_in"))
    second = mem.writer(ws, turn(ws))
    second.add(
        new,
        SupersedeItem(
            old_id=old.item_id, new_id=new.item_id, link_id=new_id(), valid_to=NOW, title="moved"
        ),
    )
    result = await second.commit()

    reader = mem.reader(ws)
    history = await reader.item(old.item_id)
    assert history is not None
    assert history.state == "superseded"
    assert history.valid_to == NOW
    current = await reader.find_items(kinds=[Kind.FACT], current_only=True)
    assert [i.text for i in current] == ["I live in Pune"]
    assert [e.op for e in result.diff.entries] == ["added", "superseded"]
    links = await reader.links([new.item_id])
    assert [(lk.src_item_id, lk.link_type.value, lk.dst_item_id) for lk in links] == [
        (new.item_id, "supersedes", old.item_id)
    ]

"""S3.2 / S3.12 / S3.14 on the memory side: a correction archives a row recorded by mistake
(not history), a turn can commit more than once, retrieval bookkeeping is not a memory write,
and items cited often enough enter the quick layer."""

from datetime import timedelta

from secondmind.core import ItemStatus, Kind, LinkType, PolicyDecision, new_id
from secondmind.memory import (
    CorrectItem,
    RenderInput,
    SetTriggerState,
    UpdateItem,
    render_verbal,
)
from tests.unit.memory.helpers import NOW, Events, create, item, memory, scope, turn


async def test_a_correction_archives_the_mistake_links_it_and_undo_restores_it() -> None:
    mem, _ = memory()
    ws = scope()
    wrong = create(item("Nisha lives in Pune", Kind.FACT, predicate="lives_in"))
    first = mem.writer(ws, turn(ws))
    first.add(wrong)
    await first.commit()

    right = create(item("Nisha lives in Mumbai", Kind.FACT, predicate="lives_in"))
    fix = turn(ws)
    events = Events()
    writer = mem.writer(ws, fix, emit=events)
    writer.add(
        right,
        CorrectItem(
            old_id=wrong.item_id, new_id=right.item_id, link_id=new_id(), title="Nisha in Pune"
        ),
    )
    result = await writer.commit()

    reader = mem.reader(ws)
    old = await reader.item(wrong.item_id)
    assert old is not None
    assert old.status is ItemStatus.ARCHIVED  # kept for the record, out of recall
    assert old.state == "current"  # not history: it was never true
    assert wrong.item_id not in {i.id for i in await reader.find_items()}
    links = await reader.links([wrong.item_id])
    assert [(lk.src_item_id, lk.link_type) for lk in links] == [(right.item_id, LinkType.CORRECTS)]
    corrected = [e for e in result.diff.entries if e.op == "corrected"]
    assert len(corrected) == 1
    assert "mistake" in corrected[0].reason
    assert [t.tool for t in events.of("tool_call")] == ["memory.create", "memory.correct"]

    await mem.undo(ws, turn(ws, kind="undo"), fix.turn_id)
    restored = await reader.item(wrong.item_id)
    assert restored is not None
    assert restored.status is ItemStatus.ACTIVE
    assert await reader.links([wrong.item_id]) == []


def test_a_mistake_says_so_in_its_verbal_key() -> None:
    wrong = item("Rohan is Nisha's husband", Kind.FACT)
    fix = item("Rohan is Nisha's cousin", Kind.FACT, mentioned_at=NOW + timedelta(days=13))
    sentence = render_verbal(RenderInput(item=wrong, timezone="Asia/Kolkata", corrected_by=fix))
    assert sentence == (
        "Recorded by mistake, corrected on 6 October 2026: Rohan is Nisha's husband. "
        "The right version: Rohan is Nisha's cousin."
    )


async def test_a_turn_that_commits_twice_continues_its_write_log_and_undoes_both() -> None:
    mem, _ = memory()
    ws = scope()
    task = create(item("Ask Nisha about her interview", Kind.TASK))
    t = turn(ws)
    first = mem.writer(ws, t)
    first.add(task)
    await first.commit()
    second = mem.writer(ws, t)
    second.add(UpdateItem(item_id=task.item_id, changes={"importance": 4}, title="interview"))
    await second.commit()

    log = await mem.reader(ws).write_log(t.turn_id)
    assert [r.seq for r in log] == list(range(1, len(log) + 1))
    assert all(r.decision is PolicyDecision.ALLOWED for r in log)

    await mem.undo(ws, turn(ws, kind="undo"), t.turn_id)
    stored = await mem.reader(ws).item(task.item_id)
    assert stored is not None
    assert stored.status is ItemStatus.DELETED


async def test_access_bookkeeping_is_not_a_memory_write() -> None:
    mem, _ = memory()
    ws = scope()
    note = create(item("The sleep article: keep screens out of the bedroom", Kind.NOTE))
    saved = turn(ws)
    writer = mem.writer(ws, saved)
    writer.add(note)
    await writer.commit()
    recall = new_id()

    await mem.record_access(
        ws, turn_id=recall, at=NOW, retrieved=[note.item_id], cited=[note.item_id]
    )

    reader = mem.reader(ws)
    stored = await reader.item(note.item_id)
    assert stored is not None
    assert stored.access_count == 1
    assert stored.last_accessed_at == NOW
    assert stored.updated_by_turn_id == saved.turn_id  # untouched: undo still sees no conflict
    assert await reader.write_log(recall) == []

    # A later write never overwrites the bookkeeping with a stale snapshot.
    edit = mem.writer(ws, turn(ws))
    edit.add(UpdateItem(item_id=note.item_id, changes={"importance": 4}, title="sleep"))
    await edit.commit()
    again = await reader.item(note.item_id)
    assert again is not None
    assert again.access_count == 1


async def test_items_cited_in_enough_recall_turns_enter_the_quick_layer() -> None:
    mem, _ = memory(quick_frequent_min=2, quick_recent_days=7)
    ws = scope()
    old = item("Gym routine Mon Wed Fri", Kind.NOTE, mentioned_at=NOW - timedelta(days=60))
    rare = item("A pasta recipe", Kind.NOTE, mentioned_at=NOW - timedelta(days=60))
    often, seldom = create(old), create(rare)
    writer = mem.writer(ws, turn(ws))
    writer.add(often, seldom)
    await writer.commit()
    for days in (1, 2):
        await mem.record_access(
            ws, turn_id=new_id(), at=NOW - timedelta(days=days), retrieved=[often.item_id],
            cited=[often.item_id],
        )  # fmt: skip
    await mem.record_access(
        ws, turn_id=new_id(), at=NOW, retrieved=[seldom.item_id], cited=[seldom.item_id]
    )

    ops = await mem.expiry_ops(ws, NOW)

    promoted = [op for op in ops if isinstance(op, UpdateItem)]
    assert [op.item_id for op in promoted] == [often.item_id]
    assert promoted[0].changes["quick_reason"] == "frequently retrieved"
    assert promoted[0].origin == "system"
    assert not any(isinstance(op, SetTriggerState) for op in ops)

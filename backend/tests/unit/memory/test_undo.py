"""S2.12: undo a turn from its write log, as a new turn; redo; conflicts; the bulk rule."""

from secondmind.core import EntityRole, ItemStatus, Kind, new_id
from secondmind.memory import FulfilIntention, SupersedeItem, UpdateItem
from tests.unit.memory.helpers import NOW, Events, create, item, memory, person, scope, turn


async def test_undo_soft_deletes_what_the_turn_created_and_redo_brings_it_back() -> None:
    mem, _ = memory()
    ws = scope()
    kabir = person("Kabir", "partner", key=True)
    gift = create(
        item("Kabir likes fountain pens", Kind.PREFERENCE), (kabir.entity_id, EntityRole.ABOUT)
    )
    saved = turn(ws)
    writer = mem.writer(ws, saved)
    writer.add(kabir, gift)
    await writer.commit()

    events = Events()
    undo = turn(ws, kind="undo")
    result = await mem.undo(ws, undo, saved.turn_id, emit=events)

    reader = mem.reader(ws)
    stored = await reader.item(gift.item_id)
    assert stored is not None
    assert stored.status is ItemStatus.DELETED  # soft delete: the row stays
    assert await reader.entity(kabir.entity_id) is not None
    assert "Kabir" not in [e.name for e in await reader.entities()]
    assert result.diff.undo_of == saved.turn_id
    assert {(e.op, e.title) for e in result.diff.entries} >= {
        ("removed", "Kabir likes fountain pens"),
        ("removed", "Kabir"),
    }
    assert events.of("memory_diff")  # the undo is its own turn with its own diff

    await mem.undo(ws, turn(ws, kind="undo"), undo.turn_id)  # undo the undo = redo

    stored = await reader.item(gift.item_id)
    assert stored is not None
    assert stored.status is ItemStatus.ACTIVE
    assert "Kabir" in [e.name for e in await reader.entities()]


async def test_undoing_a_supersede_reopens_the_old_row() -> None:
    mem, _ = memory()
    ws = scope()
    old = create(item("I live in Bengaluru", Kind.FACT, predicate="lives_in"))
    first = mem.writer(ws, turn(ws))
    first.add(old)
    await first.commit()
    new = create(item("I live in Pune", Kind.FACT, predicate="lives_in"))
    moved = turn(ws)
    second = mem.writer(ws, moved)
    second.add(
        new,
        SupersedeItem(old_id=old.item_id, new_id=new.item_id, link_id=new_id(), valid_to=NOW),
    )
    await second.commit()

    await mem.undo(ws, turn(ws, kind="undo"), moved.turn_id)

    reader = mem.reader(ws)
    reopened = await reader.item(old.item_id)
    assert reopened is not None
    assert (reopened.state, reopened.valid_to) == ("current", None)
    gone = await reader.item(new.item_id)
    assert gone is not None
    assert gone.status is ItemStatus.DELETED
    assert await reader.links([old.item_id]) == []


async def test_undoing_a_fulfil_restores_the_intention() -> None:
    mem, _ = memory()
    ws = scope()
    wish = create(item("Watch Severance", Kind.INTENTION, subtype="watch"))
    first = mem.writer(ws, turn(ws))
    first.add(wish)
    await first.commit()
    watched = create(item("I watched Severance", Kind.EPISODE))
    second_turn = turn(ws)
    second = mem.writer(ws, second_turn)
    second.add(
        watched,
        FulfilIntention(intention_id=wish.item_id, episode_id=watched.item_id, link_id=new_id()),
    )
    result = await second.commit()
    assert [e.op for e in result.diff.entries] == ["added", "fulfilled"]

    await mem.undo(ws, turn(ws, kind="undo"), second_turn.turn_id)

    reader = mem.reader(ws)
    intention = await reader.item(wish.item_id)
    assert intention is not None
    assert intention.state == "wanted"
    episode = await reader.item(watched.item_id)
    assert episode is not None
    assert episode.status is ItemStatus.DELETED


async def test_an_item_a_later_turn_changed_is_reported_as_a_conflict_and_the_rest_undone() -> None:
    mem, _ = memory()
    ws = scope()
    a = create(item("note a"))
    b = create(item("note b"))
    saved = turn(ws)
    first = mem.writer(ws, saved)
    first.add(a, b)
    await first.commit()
    later = mem.writer(ws, turn(ws))
    later.add(UpdateItem(item_id=a.item_id, changes={"text": "note a, edited"}, title="note a"))
    await later.commit()

    result = await mem.undo(ws, turn(ws, kind="undo"), saved.turn_id)

    reader = mem.reader(ws)
    kept = await reader.item(a.item_id)
    assert kept is not None
    assert (kept.status, kept.text) == (ItemStatus.ACTIVE, "note a, edited")
    removed = await reader.item(b.item_id)
    assert removed is not None
    assert removed.status is ItemStatus.DELETED
    conflicts = [e for e in result.diff.entries if e.op == "conflict"]
    assert [(e.title, e.item_id) for e in conflicts] == [("note a", a.item_id)]


async def test_a_large_undo_is_held_for_confirmation() -> None:
    mem, _ = memory(bulk_threshold=2)
    ws = scope()
    ops = [create(item(f"note {n}")) for n in range(3)]
    saved = turn(ws)
    writer = mem.writer(ws, saved)
    writer.add(*ops)
    await writer.commit()

    result = await mem.undo(ws, turn(ws, kind="undo"), saved.turn_id)

    held = [e for e in result.diff.entries if e.op == "held"]
    assert [e.rule_id for e in held] == ["P-BULK-1"]
    items = await mem.reader(ws).items([op.item_id for op in ops])
    assert all(i.status is ItemStatus.ACTIVE for i in items)  # nothing undone yet

    assert held[0].held_write_id is not None
    await mem.confirm_held(ws, turn(ws, kind="confirm"), held[0].held_write_id)

    items = await mem.reader(ws).items([op.item_id for op in ops])
    assert all(i.status is ItemStatus.DELETED for i in items)


async def test_undoing_a_turn_with_no_writes_says_so() -> None:
    mem, _ = memory()
    ws = scope()
    result = await mem.undo(ws, turn(ws, kind="undo"), new_id())
    assert [(e.op, e.title) for e in result.diff.entries] == [("not_written", "nothing to undo")]

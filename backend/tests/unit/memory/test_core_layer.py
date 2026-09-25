"""S2.9: core memory renders current state only, in a fixed section order, leaves sensitive items
out until confirmed, summarises open intentions, and stays within its token budget."""

from datetime import timedelta

from secondmind.core import EntityRole, Kind, new_id
from secondmind.memory import SupersedeItem, UpdateItem
from tests.unit.memory.helpers import NOW, create, item, memory, person, scope, sensitive, turn


async def test_core_shows_current_state_by_section_and_hides_unconfirmed_sensitive_items() -> None:
    mem, _ = memory()
    ws = scope()
    kabir = person("Kabir", "partner", key=True)
    old_home = create(item("I live in Bengaluru", Kind.FACT, predicate="lives_in", in_core=True))
    first = mem.writer(ws, turn(ws))
    first.add(old_home)
    await first.commit()

    home = create(item("I live in Pune", Kind.FACT, predicate="lives_in", in_core=True))
    veggie = create(item("I'm vegetarian", Kind.PREFERENCE, in_core=True))
    pens = create(
        item("Kabir likes fountain pens", Kind.PREFERENCE, in_core=True),
        (kabir.entity_id, EntityRole.ABOUT),
    )
    therapy = create(sensitive(item("I see a therapist", Kind.FACT)))
    shows = [create(item(f"Watch show {n}", Kind.INTENTION, subtype="watch")) for n in range(3)]
    book = create(item("Read Piranesi", Kind.INTENTION, subtype="read"))
    second = mem.writer(ws, turn(ws, now=NOW + timedelta(days=1)))
    second.add(
        kabir,
        home,
        SupersedeItem(old_id=old_home.item_id, new_id=home.item_id, link_id=new_id(), valid_to=NOW),
        veggie,
        pens,
        therapy,
        UpdateItem(item_id=therapy.item_id, changes={"in_core": True}),  # held: P-SENS-1
        *shows,
        book,
    )
    await second.commit()

    core = await mem.reader(ws).core()

    assert core.text.splitlines() == [
        "## Core memory (what I always know about you)",
        "### About me",
        "- I live in Pune",
        "### Key people",
        "- Kabir (partner):",
        "  - Kabir likes fountain pens",
        "### Preferences",
        "- I'm vegetarian",
        "### Active goals",
        "- (none yet)",
        "### Open intentions",
        "- 1 to read, 3 to watch",
        core.text.splitlines()[12],
        "### Confirmed patterns",
        "- (none yet)",
        "### Rules",
        "- (none yet)",
    ]
    assert core.text.splitlines()[12].startswith("- Most recent: ")
    assert core.text.splitlines()[12].count(";") == 2  # the three most recent
    assert not core.truncated

    # Once confirmed, the sensitive item joins core.
    [held] = await mem.reader(ws).held_writes(status="pending")
    await mem.confirm_held(ws, turn(ws, kind="confirm"), held.id)
    assert "- I see a therapist" in (await mem.reader(ws).core()).text


async def test_core_is_cut_to_the_budget_and_says_so() -> None:
    mem, _ = memory(core_token_budget=60)
    ws = scope()
    writer = mem.writer(ws, turn(ws))
    writer.add(
        *[
            create(item(f"A standing fact about me, number {n}", Kind.FACT, in_core=True))
            for n in range(6)
        ]
    )
    await writer.commit()

    core = await mem.reader(ws).core()

    assert core.truncated
    assert core.tokens <= 60
    assert core.text.endswith("- (more in the archive)")

"""S2.9 / S2.14: background housekeeping runs as system turns: stored, with events, undoable."""

import uuid
from datetime import timedelta

from secondmind.agent import TurnKind, TurnRunner, TurnStatus
from secondmind.config import DEFAULT_RESOURCES_DIR, PromptRegistry
from secondmind.core import EntityRole, KeyKind, Kind, WorkspaceScope
from secondmind.memory import EntityContent, Memory, UpsertEntity
from secondmind.memory.adapters import InMemoryMemory
from secondmind.observability import NullTracer
from secondmind.providers import FakeProvider
from tests.fakes import InMemoryTurns
from tests.unit.memory.helpers import NOW, create, item, person, turn
from tests.unit.providers.helpers import router as make_router

PROMPTS = PromptRegistry.load(DEFAULT_RESOURCES_DIR / "prompts")
SCOPE = WorkspaceScope(workspace_id=uuid.uuid4(), user_id=uuid.uuid4())
LATER = NOW + timedelta(days=8)


def runner() -> tuple[TurnRunner, InMemoryTurns, Memory]:
    turns = InMemoryTurns(clock=lambda: LATER)
    memory = Memory(InMemoryMemory().store)
    r = make_router({"primary": FakeProvider("primary")}, "primary:m", None, all_steps=True)
    return (
        TurnRunner(
            router=r,
            prompts=PROMPTS,
            stores=turns.store,
            tracer=NullTracer(),
            config_hash="c" * 64,
            max_message_chars=100,
            memory=memory,
            clock=lambda: LATER,
        ),
        turns,
        memory,
    )


async def test_quick_expiry_is_a_system_turn_with_a_diff_and_nothing_when_idle() -> None:
    tr, turns, memory = runner()
    assert await tr.expire_quick(SCOPE, timezone="Asia/Kolkata") is None
    assert turns.turns == {}

    note = create(
        item(
            "Buy stamps",
            in_quick=True,
            quick_reason="mentioned in the last 7 days",
            quick_until=NOW + timedelta(days=7),
        )
    )
    writer = memory.writer(SCOPE, turn(SCOPE))
    writer.add(note)
    await writer.commit()

    done = await tr.expire_quick(SCOPE, timezone="Asia/Kolkata")

    assert done is not None
    assert (done.kind, done.status) == (TurnKind.SYSTEM, TurnStatus.COMPLETED)
    assert done.output == "Quick layer tidied: changed 'Buy stamps'."
    events = [e.event.type for e in await turns.store(SCOPE).events(done.id)]
    assert events == ["policy", "tool_call", "memory_diff"]
    stored = await memory.reader(SCOPE).item(note.item_id)
    assert stored is not None
    assert not stored.in_quick
    assert await tr.expire_quick(SCOPE, timezone="Asia/Kolkata") is None


async def test_renaming_an_entity_rerenders_linked_keys_as_a_system_turn() -> None:
    tr, turns, memory = runner()
    nisha = person("Nisha", "sister")
    tulips = create(
        item("Nisha likes tulips", Kind.PREFERENCE), (nisha.entity_id, EntityRole.ABOUT)
    )
    writer = memory.writer(SCOPE, turn(SCOPE))
    writer.add(nisha, tulips)
    await writer.commit()
    await memory.keys(SCOPE, timezone="UTC", embed=None, model="none").rebuild([tulips.item_id])

    rename = memory.writer(SCOPE, turn(SCOPE))
    rename.add(
        UpsertEntity(
            entity_id=nisha.entity_id,
            entity=EntityContent(kind=nisha.entity.kind, name="Nisha", labels=["cousin"]),
            create=False,
        )
    )
    result = await rename.commit()
    assert result.changed_entities == {nisha.entity_id}

    done = await tr.rerender_entity_keys(
        SCOPE, entity_ids=sorted(result.changed_entities), timezone="UTC"
    )

    assert (done.kind, done.status) == (TurnKind.SYSTEM, TurnStatus.COMPLETED)
    assert done.output == "Re-rendered the search keys of 1 memory."
    keys = await memory.reader(SCOPE).keys([tulips.item_id])
    verbal = next(k.text for k in keys if k.key_kind is KeyKind.VERBAL)
    assert "Nisha (cousin)" in verbal
    assert "sister" not in verbal
    assert done.id in turns.turns

"""S3.10: person triggers fire on a mention (name or label), topic and situation triggers on a
close enough message; firing is a write that undo reverses."""

import uuid
from collections.abc import Sequence

from secondmind.core import Kind, NullTrail, TriggerOn, TriggerState, new_id
from secondmind.memory import NewTrigger, TriggerContent, WriterTurn, content_hash
from secondmind.providers import FakeProvider
from secondmind.retrieval import TriggerCheck
from tests.unit.memory.helpers import create, item
from tests.unit.retrieval.helpers import NOW, Sink, World, world

_FAKE = FakeProvider()


async def embed(texts: Sequence[str]) -> list[list[float]] | None:
    return (await _FAKE.embed("fake-embed", list(texts), 256)).vectors


def turn(w: World) -> WriterTurn:
    return WriterTurn(
        turn_id=new_id(), workspace_id=w.scope.workspace_id, kind="user", now=NOW.instant
    )


async def add_trigger(w: World, text: str, on: TriggerOn, spec: dict[str, str]) -> uuid.UUID:
    op = create(item(text, Kind.TASK)).model_copy(
        update={
            "triggers": [NewTrigger(trigger_id=new_id(), trigger=TriggerContent(on=on, spec=spec))]
        }
    )
    writer = w.memory.writer(w.scope, turn(w), confirmed=True)
    writer.add(op)
    await writer.commit()
    return op.item_id


async def check(w: World, message: str, threshold: float = 0.6) -> list[str]:
    fired = await TriggerCheck(threshold=threshold).run(
        memory=w.memory,
        scope=w.scope,
        turn=turn(w),
        message=message,
        vector=None,
        embed=embed,
        trail=NullTrail(Sink()),
    )
    return [f.note for f in fired]


async def state(w: World, item_id: uuid.UUID) -> TriggerState:
    (t,) = await w.memory.reader(w.scope).triggers([item_id])
    return t.state


async def nisha_trigger(w: World) -> uuid.UUID:
    spec = {"entity_id": str(w.ids["nisha"]), "name": "Nisha"}
    return await add_trigger(w, "Ask Nisha about her interview", TriggerOn.PERSON, spec)


async def test_a_person_trigger_fires_on_her_name_once() -> None:
    w = await world()
    task = await nisha_trigger(w)
    assert await check(w, "Had lunch with Nisha today") == [
        "By the way, you wanted to ask Nisha about her interview."
    ]
    assert await state(w, task) is TriggerState.FIRED
    assert await check(w, "Nisha called again") == []


async def test_a_person_trigger_fires_on_her_label() -> None:
    w = await world()
    task = await nisha_trigger(w)
    assert len(await check(w, "I'm seeing my sister tonight")) == 1
    assert await state(w, task) is TriggerState.FIRED


async def test_an_unrelated_turn_fires_nothing() -> None:
    w = await world()
    task = await nisha_trigger(w)
    assert await check(w, "What's on my watch list?") == []
    assert await state(w, task) is TriggerState.PENDING


async def test_a_topic_trigger_fires_above_the_threshold_and_not_below() -> None:
    cue = "planning the Japan trip"
    spec = {"cue": cue, "cue_hash": content_hash(cue)}
    w = await world()
    task = await add_trigger(w, "Remember Ichiban Ramen Bar", TriggerOn.TOPIC, spec)
    assert await check(w, "I'm planning the Japan trip, any ideas?") != []
    assert await state(w, task) is TriggerState.FIRED

    w = await world()
    task = await add_trigger(w, "Remember Ichiban Ramen Bar", TriggerOn.TOPIC, spec)
    assert await check(w, "I'm planning the Japan trip, any ideas?", threshold=0.99) == []
    assert await check(w, "What should I cook tonight?") == []
    assert await state(w, task) is TriggerState.PENDING


async def test_a_situation_trigger_is_matched_the_same_way_as_a_topic() -> None:
    cue = "packing for a long flight"
    spec = {"cue": cue, "cue_hash": content_hash(cue)}
    w = await world()
    task = await add_trigger(w, "Take the neck pillow", TriggerOn.SITUATION, spec)
    assert await check(w, "What should I cook tonight?") == []
    assert await state(w, task) is TriggerState.PENDING
    assert await check(w, "I'm packing for a long flight tomorrow") != []
    assert await state(w, task) is TriggerState.FIRED


async def test_undo_puts_a_fired_trigger_back_to_pending() -> None:
    w = await world()
    task = await nisha_trigger(w)
    t = turn(w)
    fired = await TriggerCheck(threshold=0.6).run(
        memory=w.memory,
        scope=w.scope,
        turn=t,
        message="Nisha says hi",
        vector=None,
        embed=embed,
        trail=NullTrail(Sink()),
    )
    assert fired
    undo = WriterTurn(
        turn_id=new_id(), workspace_id=w.scope.workspace_id, kind="undo", now=NOW.instant
    )
    await w.memory.undo(w.scope, undo, t.turn_id)
    assert await state(w, task) is TriggerState.PENDING

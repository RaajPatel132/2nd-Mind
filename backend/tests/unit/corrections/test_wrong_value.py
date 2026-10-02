"""S3.12: a corrected memory keeps the new words as its text, and a title that says them."""

from secondmind.core import Kind, NullTrail
from secondmind.corrections import CorrectContext, Corrector
from secondmind.corrections.schemas import CorrectionOut, CorrectionTarget
from secondmind.ingestion import TurnNow
from secondmind.memory import CreateItem
from tests.unit.memory.helpers import NOW, create, item, memory, scope, turn
from tests.unit.retrieval.helpers import RecordingStore, Sink, fake, steps


def _fix(new_text: str) -> CorrectionOut:
    return CorrectionOut(
        type="wrong_value",
        target=CorrectionTarget(source="previous_turn", ref="p1", query=None),
        changes=None,
        new_text=new_text,
        relation=None,
        match=None,
        category=None,
        rule_note=None,
        assumption=None,
        reason="the person said so",
    )


async def _new_item_after_correcting(new_text: str) -> CreateItem:
    mem, _ = memory()
    ws = scope()
    old = create(item("I live in Bengaluru", Kind.FACT))
    writer = mem.writer(ws, turn(ws))
    writer.add(old)
    await writer.commit()
    record = await mem.reader(ws).item(old.item_id)
    assert record is not None
    ctx = CorrectContext(
        scope=ws,
        turn_id=turn(ws).turn_id,
        message="no, that's wrong",
        now=TurnNow(NOW, "Asia/Kolkata"),
        steps=steps(fake()),
        memory=mem,
        store=RecordingStore(),
        trail=NullTrail(Sink()),
        write=lambda _: None,
    )
    ops = await Corrector()._wrong_value(ctx, record, _fix(new_text), [])
    [created] = [op for op in ops if isinstance(op, CreateItem)]
    return created


async def test_a_short_correction_is_its_own_title() -> None:
    created = await _new_item_after_correcting("I live in Pune")
    assert created.item.title == "I live in Pune"
    assert created.title == "I live in Pune"


async def test_a_long_correction_gets_a_title_of_its_own_words_not_the_old_one() -> None:
    words = "I moved to Pune last spring for a job at a small studio near the river and I rent"
    text = f"{words} a flat above a bakery with my sister"
    assert len(text) > 80
    created = await _new_item_after_correcting(text)
    assert created.item.text == text  # the full words are kept
    title = created.item.title
    assert title != "I live in Bengaluru"
    assert len(title) <= 80
    assert title.endswith("…")
    assert text.startswith(title.removesuffix("…"))
    assert not title.removesuffix("…").endswith(" ")  # cut at a word, not mid-way
    assert created.title == title

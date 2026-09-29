"""R.3: the same memory renders and ranks the same way every time. Ties used to break on a random
id, so two identical runs sent different prompts (a response cache could not recognise them, and
a provider's prompt cache would miss on a prefix that only moved around)."""

from secondmind.core import Shape, new_id
from secondmind.memory import render_core
from secondmind.retrieval import Hit, fuse
from tests.unit.retrieval.helpers import world

WORLDS = 10  # each has new random ids: the old order flipped in about half of them


async def test_equal_candidates_rank_by_their_words_not_their_ids() -> None:
    rankings = set()
    for _ in range(WORLDS):
        w = await world()
        reader = w.memory.reader(w.scope)
        items = {i.id: i for i in await reader.items([w.ids["pune"], w.ids["jazz"]])}
        channels = {"a": [Hit(w.ids["pune"], 1)], "b": [Hit(w.ids["jazz"], 1)]}
        ranked = fuse(channels, items, shape=Shape.ENTITY, rrf_k=60, history_demotion=0.5)
        rankings.add(tuple(items[c.item_id].title for c in ranked))
    assert rankings == {("I live in Pune", "Rohan loves live jazz")}


async def test_the_core_view_orders_items_saved_together_by_their_words() -> None:
    texts = set()
    for _ in range(WORLDS):
        w = await world()
        (jazz,) = await w.memory.reader(w.scope).items([w.ids["jazz"]])
        # Same moment, as when one message saves two things: the order can't depend on the ids.
        prefs = [
            jazz.model_copy(update={"id": new_id(), "title": t, "text": t, "in_core": True})
            for t in ("I like tea", "I like coffee")
        ]
        core = render_core(prefs, entities={}, item_entities={}, open_intentions=[], budget=10_000)
        texts.add(core.text)
    assert len(texts) == 1
    assert next(iter(texts)).index("I like coffee") < next(iter(texts)).index("I like tea")

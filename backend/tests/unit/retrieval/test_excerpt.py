"""S4.7: a cited passage shows the sentences that answer the question, not just its first words."""

from secondmind.retrieval.excerpt import excerpt

PASSAGE = (
    "Sleep researchers keep finding the same thing: a cool, dark room matters. "
    "Caffeine is the quiet saboteur, and a coffee at four can still be in the body at bedtime. "
    "A short wind-down helps more than it sounds. "
    "One study of shift workers found that a lavender pillow spray made no measurable difference. "
    "If you cannot sleep after twenty minutes, get up and sit somewhere dim."
)


def test_a_short_passage_is_shown_whole() -> None:
    assert excerpt("A cool, dark room.  Matters.", "anything", 240) == "A cool, dark room. Matters."


def test_the_sentence_that_matches_the_question_is_the_one_shown() -> None:
    shown = excerpt(PASSAGE, "What about lavender in my sleep tips?", 160)
    assert "lavender pillow spray" in shown
    assert len(shown) <= 160 + 4  # the room left for the ellipses
    assert shown.startswith("…")  # it doesn't begin where the passage does


def test_the_start_is_shown_when_nothing_matches() -> None:
    shown = excerpt(PASSAGE, "xylophone quartz", 120)
    assert shown.startswith("Sleep researchers")
    assert shown.endswith("…")


def test_one_long_sentence_is_cut_around_the_matching_word() -> None:
    sentence = "word " * 80 + "the lavender is here " + "word " * 80
    shown = excerpt(sentence, "lavender", 100)
    assert "lavender" in shown
    assert len(shown) <= 100 + 4

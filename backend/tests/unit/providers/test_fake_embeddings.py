"""S3.1: offline embeddings mean something. The fake provider's vectors are a feature-hashed bag
of words, so recall can be tested (and demoed) without a real embedding model."""

import math

from secondmind.providers import FakeProvider


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def _vectors(*texts: str, dim: int = 1536) -> list[list[float]]:
    return (await FakeProvider().embed("fake-embed", texts, dim)).vectors


async def test_sentences_that_share_words_are_closer_than_unrelated_ones() -> None:
    run, runs, bag = await _vectors(
        "Logged run: I ran 5 km in 31 minutes on Saturday 12 September 2026.",
        "How many runs did I log in September?",
        "Kabir liked the MK bag, serial 123ABC.",
    )
    assert _cos(run, runs) > 0.1
    assert _cos(run, bag) == 0.0


async def test_vectors_are_deterministic_unit_length_and_sized() -> None:
    first = await _vectors("Where do I live?", dim=768)
    again = await _vectors("Where do I live?", dim=768)
    assert first == again
    assert len(first[0]) == 768
    assert math.isclose(math.sqrt(sum(v * v for v in first[0])), 1.0, rel_tol=1e-9)


async def test_a_text_with_no_words_still_gets_a_unit_vector() -> None:
    [vector] = await _vectors("?!")
    assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)


async def test_stopwords_alone_do_not_make_sentences_similar() -> None:
    a, b = await _vectors("What is it that you do?", "It is what it is, you know")
    assert abs(_cos(a, b)) < 0.5

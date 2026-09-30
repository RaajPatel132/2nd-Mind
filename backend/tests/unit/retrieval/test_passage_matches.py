"""S4.7: a memory found by what its saved page says is judged by the reranker on its summary alone
(a page's words reach a model only at save and in the answer), so its score can't sink it."""

import uuid

from secondmind.retrieval.fusion import Candidate
from secondmind.retrieval.select import PASSAGE_KEPT, keep_passage_matches


def cand(**fields: object) -> Candidate:
    return Candidate(item_id=uuid.uuid4(), **fields)  # type: ignore[arg-type]


def test_a_passage_the_words_matched_is_lifted_to_the_floor() -> None:
    page = cand(snippet="the old mill", snippet_position=3, lexical=0.4, rerank_score=0.17)
    keep_passage_matches([page], 0.5)
    assert page.rerank_score == 0.5
    assert page.rerank_reason == PASSAGE_KEPT


def test_a_better_score_is_left_alone() -> None:
    page = cand(snippet="the old mill", snippet_position=3, lexical=0.4, rerank_score=0.9)
    keep_passage_matches([page], 0.5)
    assert page.rerank_score == 0.9
    assert page.rerank_reason == ""


def test_a_memory_without_a_passage_match_keeps_its_low_score() -> None:
    plain = cand(rerank_score=0.17)
    dense_only = cand(snippet="the old mill", snippet_position=3, lexical=None, rerank_score=0.17)
    keep_passage_matches([plain, dense_only], 0.5)
    assert plain.rerank_score == 0.17
    assert dense_only.rerank_score == 0.17  # similarity alone is not evidence the words matched


def test_a_passage_match_the_reranker_did_not_score_is_kept() -> None:
    page = cand(snippet="the old mill", snippet_position=3, lexical=0.2)
    keep_passage_matches([page], 0.5)
    assert page.rerank_score == 0.5

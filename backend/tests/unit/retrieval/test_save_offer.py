"""S3.9: only what I said is offered for saving, and a "yes" saves those words."""

import uuid
from datetime import UTC, datetime

from secondmind.retrieval import SAVE_OFFER, Evidence, save_message
from secondmind.retrieval.answer import save_offer

AT = datetime(2026, 9, 29, 13, 30, tzinfo=UTC)
MINE, YOURS = uuid.uuid4(), uuid.uuid4()


def said(marker: int, turn: uuid.UUID, role: str, text: str) -> Evidence:
    return Evidence(
        marker=marker,
        kind="turn",
        title=text[:20],
        turn_id=turn,
        role=role,  # type: ignore[arg-type]
        said=text,
        said_at=AT,
    )


def test_only_my_cited_words_are_offered_once_each() -> None:
    books = "1. Tea by the Window by Arun Mehra: gentle short stories."
    cited = [
        said(1, MINE, "assistant", books),
        said(2, YOURS, "user", "Can you suggest three books?"),
        said(3, MINE, "assistant", books),
    ]
    offer = save_offer(cited)
    assert offer is not None
    assert offer.turn_ids == [MINE]
    assert offer.said == [books]
    assert offer.offer == SAVE_OFFER
    assert save_message(offer) == f"Save what you suggested:\n{books}"


def test_nothing_i_said_means_no_offer() -> None:
    assert save_offer([said(1, YOURS, "user", "If I get the Berlin offer I'll move.")]) is None
    assert save_offer([]) is None

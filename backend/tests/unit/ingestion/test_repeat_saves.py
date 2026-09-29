"""A wish saved twice is one wish (R.3): the model's tags differ between two readings of the same
sentence, and a tag alone must not turn "Already saved" into "Added detail"."""

import uuid
from datetime import UTC, datetime
from typing import Any

from secondmind.core import Kind
from secondmind.ingestion.reconcile import Draft, adds_detail
from secondmind.memory import ItemContent, ItemRecord

NOW = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)


def _content(**over: Any) -> ItemContent:
    base: dict[str, Any] = {
        "kind": Kind.INTENTION,
        "subtype": "watch",
        "state": "wanted",
        "title": "Dark",
        "text": "I want to watch Dark.",
        "mentioned_at": NOW,
        "tags": ["series"],
    }
    return ItemContent(**{**base, **over})


def _stored(**over: Any) -> ItemRecord:
    return ItemRecord(
        **_content(**over).model_dump(),
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        created_at=NOW,
        updated_at=NOW,
        created_by_turn_id=uuid.uuid4(),
        updated_by_turn_id=uuid.uuid4(),
    )


def _draft(**over: Any) -> Draft:
    return Draft(ref="m1", item_id=uuid.uuid4(), content=_content(**over))


def test_a_tag_alone_is_not_a_detail() -> None:
    """Saving Dark twice must not read "Added detail" on the days the model adds a tag."""
    assert not adds_detail(_stored(), _draft(tags=["watch", "series"]))
    assert not adds_detail(_stored(), _draft(tags=[]))


def test_an_attribute_or_a_summary_is_a_detail() -> None:
    assert adds_detail(_stored(), _draft(attributes={"language": "German"}))
    assert adds_detail(_stored(), _draft(summary="A German mystery series."))
    assert not adds_detail(
        _stored(attributes={"language": "German"}), _draft(attributes={"language": "German"})
    )

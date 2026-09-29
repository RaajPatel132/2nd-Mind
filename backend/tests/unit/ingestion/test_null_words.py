"""A model that writes the word for null where a field allows null means null (R.3): Haiku 4.5
wrote "subtype": "null" and the word became a vocabulary term."""

from typing import Any

from secondmind.ingestion.schemas import ExtractedEntity, ProposedMemory


def _proposed(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ref": "m1",
        "kind": "preference",
        "subtype": None,
        "format": None,
        "state": None,
        "title": "Bag from Michael Kors",
        "text": "My wife liked this bag.",
        "summary": None,
        "category": None,
        "tags": [],
        "attributes": [],
        "subject": None,
        "predicate": None,
        "value": None,
        "entities": [],
        "times": [],
        "reminder": None,
        "trigger": None,
        "modality": "asserted",
        "sentiment": None,
        "rating": None,
        "sensitivity": "normal",
        "importance": 3,
        "links": [],
        "layer": "quick",
        "layer_rationale": "x",
        "rationale": "x",
    }
    return {**base, **over}


def test_the_word_null_where_null_is_allowed_means_null() -> None:
    """Haiku 4.5 wrote "subtype": "null"; taken as text it became a vocabulary term."""
    m = ProposedMemory.model_validate(
        _proposed(subtype="null", state="None", category=" NULL ", summary="")
    )
    assert (m.subtype, m.state, m.category, m.summary) == (None, None, None, None)


def test_the_word_stays_where_null_is_not_allowed() -> None:
    """A title that is the word "None" is a title; only nullable fields read the word as null."""
    m = ProposedMemory.model_validate(_proposed(title="None", subtype="gift"))
    assert m.title == "None"
    assert m.subtype == "gift"
    entity = ExtractedEntity.model_validate(
        {"id": "e1", "mention": "null", "name": "null", "kind": "person", "label": "None"}
    )
    assert entity.mention == "null"
    assert entity.name is None
    assert entity.label is None

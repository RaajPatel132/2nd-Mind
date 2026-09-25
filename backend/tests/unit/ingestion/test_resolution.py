"""S2.6 normalisation and S2.7 entity resolution, in code after the model. All data synthetic."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from secondmind.core import EntityKind
from secondmind.ingestion import (
    EntityChoice,
    ExtractedEntity,
    Term,
    normalise,
    resolve_entities,
    terms_for,
)
from secondmind.memory import EntityRecord

NOW = datetime(2026, 9, 23, 4, 30, tzinfo=UTC)
WS = uuid.uuid4()


def entity(name: str, kind: EntityKind = EntityKind.PERSON, *labels: str) -> EntityRecord:
    return EntityRecord(
        id=uuid.uuid4(),
        workspace_id=WS,
        kind=kind,
        name=name,
        labels=list(labels),
        created_at=NOW,
        updated_at=NOW,
        created_by_turn_id=None,
        updated_by_turn_id=None,
    )


ME = entity("me", EntityKind.SELF)


def mention(text: str, *, name: str | None = None, label: str | None = None) -> ExtractedEntity:
    return ExtractedEntity(id="e1", mention=text, name=name, kind="person", label=label)


# ------------------------------------------------------------------ normalisation


async def test_plurals_and_separators_reuse_the_existing_slug() -> None:
    slug, how = await normalise("TV-Shows", [Term("tv_show")], vocab="subtype")
    assert (slug, how.reused) == ("tv_show", True)
    assert how.how == "reused tv_show instead of new tv_shows"


async def test_similar_meaning_reuses_the_existing_slug() -> None:
    async def similar(text: str, labels: Sequence[str]) -> tuple[int, float] | None:
        assert text == "shows"
        return labels.index("entertainment"), 0.91

    slug, how = await normalise(
        "shows", [Term("entertainment")], vocab="category", similarity=similar, path=True
    )
    assert slug == "entertainment"
    assert how.how == "reused entertainment instead of new shows (similarity 0.91)"


async def test_nothing_similar_creates_a_new_slug() -> None:
    async def unlike(text: str, labels: Sequence[str]) -> tuple[int, float] | None:
        return 0, 0.42

    slug, how = await normalise(
        "Personal Finance", [Term("health")], vocab="category", similarity=unlike, path=True
    )
    assert (slug, how.reused, how.how) == ("personal-finance", False, "new: nothing similar")


def test_workspace_terms_come_before_built_in_ones() -> None:
    terms = terms_for("subtype", [("gift", ["present"])])
    assert terms[0] == Term("gift", ("present",))
    assert "gift" not in [t.slug for t in terms[1:]]


# ------------------------------------------------------------------ entities


async def test_first_person_attaches_to_self() -> None:
    plans = await resolve_entities(
        [ExtractedEntity(id="e1", mention="I", name=None, kind="self", label=None)],
        [],
        ME,
        now=NOW,
    )
    assert plans["e1"].entity_id == ME.id


async def test_no_match_creates_a_new_person_named_by_the_label() -> None:
    plans = await resolve_entities([mention("my wife", label="wife")], [], ME, now=NOW)
    plan = plans["e1"]
    assert plan.op is not None
    assert plan.op.create
    assert plan.resolution.outcome == "new"
    assert plan.resolution.rationale == "'my wife' → no match → new person *wife*"


async def test_a_later_name_updates_the_same_person_instead_of_adding_one() -> None:
    # Created from "my wife" earlier: a placeholder until the real name is known.
    wife = entity("wife", EntityKind.PERSON, "wife").model_copy(
        update={"attributes": {"name_known": False}}
    )
    plans = await resolve_entities(
        [mention("my wife Kabir", name="Kabir", label="wife")], [wife], ME, now=NOW
    )
    plan = plans["e1"]
    assert plan.entity_id == wife.id
    assert plan.resolution.outcome == "updated"
    assert plan.op is not None
    assert not plan.op.create
    assert plan.op.entity.name == "Kabir"


async def test_ambiguous_mentions_ask_the_chooser_and_record_the_others() -> None:
    work = entity("Rohan", EntityKind.PERSON, "colleague")
    cousin = entity("Rohan", EntityKind.PERSON, "cousin")
    asked: list[list[str]] = []

    async def choose(x: ExtractedEntity, candidates: Sequence[EntityRecord]) -> tuple[int, str]:
        asked.append([c.display for c in candidates])
        return 1, EntityChoice(candidate="Rohan (cousin)", reason="family context").reason

    plans = await resolve_entities([mention("Rohan", name="Rohan")], [work, cousin], ME, now=NOW)
    assert plans["e1"].entity_id == work.id  # without a chooser: best guess, flagged
    assert plans["e1"].resolution.outcome == "ambiguous"

    plans = await resolve_entities(
        [mention("Rohan", name="Rohan")], [work, cousin], ME, now=NOW, choose=choose
    )
    plan = plans["e1"]
    assert asked == [["Rohan (colleague)", "Rohan (cousin)"]]
    assert plan.entity_id == cousin.id
    assert plan.resolution.outcome == "ambiguous"
    assert plan.resolution.candidates == ["Rohan (colleague)"]
    assert "picked Rohan (cousin): family context" in plan.resolution.rationale

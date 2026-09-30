"""S4.10: the Aditi Rao seed is clean. No payment talk, nothing that goes stale when the seed is
moved in time, and every item kind and every input kind is present."""

import pytest

from secondmind.core import Kind
from secondmind.persona import (
    PersonaSeed,
    SeriesSpec,
    WorkspaceSpec,
    coverage,
    lint_spec,
    load_persona,
    missing,
    seed_hash,
)
from secondmind.persona.lint import input_kind


@pytest.fixture(scope="module")
def seed() -> PersonaSeed:
    return load_persona()


def test_the_seed_loads_with_its_anchor_and_both_suggested_prompts(seed: PersonaSeed) -> None:
    assert seed.id == "aditi-rao"
    assert seed.timezone == "Asia/Kolkata"
    assert seed.prompts.save.startswith("Kabir mentioned")
    assert seed.prompts.recall == "What could I get Kabir for his birthday?"
    assert seed.anchor.isoformat() == "2026-10-05T04:30:00+00:00"  # a Monday, 10:00 in Bengaluru


def test_it_holds_150_to_250_items_across_every_kind(seed: PersonaSeed) -> None:
    expanded = seed.expanded()
    assert 150 <= len(expanded.items) <= 250
    kinds, _ = coverage(seed)
    assert kinds == set(Kind), f"missing kinds: {sorted(k.value for k in set(Kind) - kinds)}"


def test_it_holds_every_input_kind(seed: PersonaSeed) -> None:
    _, inputs = coverage(seed)
    assert inputs >= {"link-full", "link-partial", "video", "image", "pdf"}
    items = seed.expanded().items
    assert sum(input_kind(i) == "link-full" for i in items) >= 3
    assert sum(input_kind(i) == "link-partial" for i in items) >= 3
    assert sum(input_kind(i) == "video" for i in items) >= 2
    assert not missing(seed)


def test_full_links_have_passages_and_files_are_stored_already_extracted(seed: PersonaSeed) -> None:
    for item in seed.expanded().items:
        src = item.source
        if src is None:
            continue
        if src.kind == "link" and src.status == "full":
            assert len(src.passages) >= 2, item.key
        if src.kind in ("image", "pdf"):
            assert src.extracted, item.key
            assert not src.url, item.key  # no original file until S9


def test_the_text_is_clean(seed: PersonaSeed) -> None:
    """No payment, refund or billing words; no weekday, month, date or relative day in the text of
    anything that doesn't recur, so moving the seed in time can't make it wrong."""
    assert lint_spec(seed) == []


def test_kabir_has_no_birthday_yet(seed: PersonaSeed) -> None:
    """The suggested save adds it, and the suggested recall then uses it (FR-13.5)."""
    for item in seed.expanded().items:
        for_kabir = any(r.entity == "kabir" and r.role.value == "for" for r in item.entities)
        assert not for_kabir or item.subtype != "birthday", item.key
        assert "kabir's birthday" not in f"{item.title} {item.text}".lower(), item.key


def test_the_moved_fact_is_superseded_so_history_has_something_to_show(seed: PersonaSeed) -> None:
    supersedes = {(lk.src, lk.dst) for lk in seed.links if lk.type.value == "supersedes"}
    assert ("live_bengaluru", "live_pune") in supersedes


def test_there_are_time_person_and_topic_triggers_and_one_sensitive_item(
    seed: PersonaSeed,
) -> None:
    items = seed.expanded().items
    on = {t.on.value for i in items for t in i.triggers}
    assert {"time", "person", "topic"} <= on
    assert sum(i.sensitivity.value == "sensitive" for i in items) == 1
    assert any(lk.type.value == "because" for lk in seed.links)


def test_every_reference_resolves(seed: PersonaSeed) -> None:
    spec = seed.expanded()
    entities = {e.key for e in spec.entities} | {"self"}
    items = {i.key for i in spec.items}
    for item in spec.items:
        for role in item.entities:
            assert role.entity in entities, f"{item.key} -> {role.entity}"
        if item.subject:
            assert item.subject in entities, f"{item.key} subject {item.subject}"
        for t in item.triggers:
            assert t.entity is None or t.entity in entities
    for lk in spec.links:
        assert lk.src in items, lk.src
        assert lk.dst in items, lk.dst
    for rel in spec.relations:
        assert {rel.src, rel.dst} <= entities
        assert rel.evidence is None or rel.evidence in items


def test_the_lint_does_catch_what_it_is_for() -> None:
    bad = WorkspaceSpec(
        series=[
            SeriesSpec(
                count=1,
                item={
                    "key": "x",
                    "kind": "note",
                    "title": "t",
                    "text": "I paid the bill on Tuesday, 12 March, and will go tomorrow.",
                    "mentioned": "-1d",
                },
            )
        ]
    )
    found = " ".join(lint_spec(bad))
    assert "money" in found
    assert "weekday" in found
    assert "literal date" in found
    assert "relative day" in found


def test_the_seed_hash_changes_with_the_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text("x: 1\n")
    b.write_text("x: 2\n")
    assert seed_hash(a) != seed_hash(b)
    assert seed_hash(a) == seed_hash(a)

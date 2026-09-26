"""The recall pipeline with a recording store (S3.4-S3.8): plan -> tools -> fusion -> answer."""

from datetime import UTC, datetime
from typing import Any

import pytest

from secondmind.core import Shape, ToolCallEvent
from secondmind.providers import FakeOutcome, ProviderErrorKind
from secondmind.retrieval import (
    SHAPE_TOOLS,
    Filters,
    Hit,
    LookupResult,
    RecallSettings,
    fuse,
)
from tests.unit.retrieval.helpers import RecordingStore, Sink, fake, plan, run_recall, world


def ran_tools(sink: Sink) -> set[str]:
    return {
        e.tool.removeprefix("recall.").split(":")[0]
        for e in sink.events
        if isinstance(e, ToolCallEvent) and e.access == "read" and e.tool != "recall.anchor"
    }


def test_shape_tools_are_pinned_in_code() -> None:
    assert {s.value: t for s, t in SHAPE_TOOLS.items()} == {
        "exact": ("lookup",),
        "list": ("lookup",),
        "latest": ("lookup", "history"),
        "history": ("history",),
        "time_window": ("timeline", "search"),
        "order": ("timeline",),
        "count": ("aggregate",),
        "set": ("lookup",),
        "entity": ("entity", "lookup"),
        "semantic": ("search",),
        "why": ("history",),
        "situational": ("lookup", "timeline"),
        "conversation": ("conversation",),
    }
    assert set(SHAPE_TOOLS) == set(Shape)


LIVES = {"predicate": "lives_in", "current_only": True}
CASES: list[tuple[str, dict[str, Any], set[str]]] = [
    ("Where do I live?", {"shape": "exact", "filters": LIVES}, {"lookup"}),
    (
        "What's on my watch list?",
        {"shape": "list", "filters": {"kinds": ["intention"], "subtypes": ["watch"]}},
        {"lookup"},
    ),
    ("Where do I live now?", {"shape": "latest", "filters": LIVES}, {"lookup", "history"}),
    ("Where have I lived?", {"shape": "history", "filters": LIVES}, {"history"}),
    (
        "What did I do last week?",
        {"shape": "time_window", "times": [{"expression": "last week", "clock": "occurred"}]},
        {"timeline"},
    ),
    (
        "Which runs did I log last week?",
        {
            "shape": "time_window",
            "about": "runs",
            "times": [{"expression": "last week", "clock": "occurred"}],
        },
        {"timeline", "search"},
    ),
    (
        "What did I do first in September?",
        {"shape": "order", "times": [{"expression": "in September", "clock": "occurred"}]},
        {"timeline"},
    ),
    (
        "How many runs in September?",
        {
            "shape": "count",
            "filters": {"subtypes": ["measurement"]},
            "aggregate": {"op": "count"},
            "times": [{"expression": "in September", "clock": "occurred"}],
        },
        {"aggregate"},
    ),
    (
        "Which shows haven't I watched?",
        {
            "shape": "set",
            "filters": {"kinds": ["intention"]},
            "set": {"op": "without", "other_kind": "episode"},
        },
        {"lookup"},
    ),
    (
        "What do I know about Nisha?",
        {"shape": "entity", "entities": [{"mention": "Nisha"}]},
        {
            "entity",
            "lookup",
        },
    ),
    ("Anything about sleep?", {"shape": "semantic"}, {"search"}),
    ("Why did I cancel the gym?", {"shape": "why"}, {"history"}),
    (
        "What should I do tonight?",
        {
            "shape": "situational",
            "filters": {"kinds": ["intention"]},
            "times": [{"expression": "tonight", "clock": "occurred", "direction": "future"}],
        },
        {"lookup", "timeline"},
    ),
    ("What did you tell me about ramen?", {"shape": "conversation"}, {"conversation"}),
]


@pytest.mark.parametrize(("question", "sub", "tools"), CASES, ids=[c[1]["shape"] for c in CASES])
async def test_each_shape_runs_its_tools_next_to_the_soft_channel(
    question: str, sub: dict[str, Any], tools: set[str]
) -> None:
    w = await world()
    ran = await run_recall(w, question, fake(("plan", plan({"question": question, **sub}))))
    assert ran_tools(ran.events) == tools | {"soft"}
    event = ran.events.of("retrieval")[0]
    assert event.plan_source == "model"
    assert event.sub_queries[0].shape == sub["shape"]


async def test_tool_arguments_come_from_the_resolved_plan() -> None:
    w = await world()
    ran = await run_recall(
        w,
        "How many runs in September?",
        fake(
            (
                "plan",
                plan(
                    {
                        "question": "How many runs in September?",
                        "shape": "count",
                        "filters": {
                            "subtypes": ["measurement"],
                            "attribute": {"key": "activity", "value": "run"},
                        },
                        "aggregate": {"op": "count"},
                        "times": [{"expression": "in September", "clock": "occurred"}],
                    }
                ),
            )
        ),
    )
    (call,) = ran.store.of("aggregate")
    filters: Filters = call.args[0]
    assert filters.subtypes == ("measurement",)
    assert filters.attribute == ("activity", "run")
    assert filters.window is not None
    assert filters.window.start is not None
    assert filters.window.start.isoformat() == "2026-08-31T18:30:00+00:00"  # 1 Sep IST
    assert call.kwargs["op"] == "count"
    assert call.kwargs["timezone"] == "Asia/Kolkata"


async def test_soft_channel_can_be_switched_off() -> None:
    w = await world()
    ran = await run_recall(
        w,
        "Where do I live?",
        fake(("plan", plan({"question": "Where do I live?", "shape": "exact", "filters": LIVES}))),
        settings=RecallSettings(soft_channel_enabled=False),
    )
    assert ran_tools(ran.events) == {"lookup"}
    assert ran.events.of("retrieval")[0].soft_channel is False


async def test_invalid_plan_is_retried_once_then_falls_back_to_meaning() -> None:
    w = await world()
    worked_out = plan(
        {
            "question": "What did I do last week?",
            "shape": "time_window",
            "times": [{"expression": "2026-09-28 to 2026-10-04", "clock": "occurred"}],
        }
    )
    ok = plan(
        {
            "question": "What did I do last week?",
            "shape": "time_window",
            "times": [{"expression": "last week", "clock": "occurred"}],
        }
    )
    provider = fake(("plan", worked_out))
    provider.script.rules[-1].outcomes.append(ok)
    ran = await run_recall(w, "What did I do last week?", provider)
    event = ran.events.of("retrieval")[0]
    assert event.plan_source == "retry"
    assert "2026-09-28" in event.plan_note

    ran = await run_recall(w, "What did I do last week?", fake(("plan", worked_out)))
    event = ran.events.of("retrieval")[0]
    assert event.plan_source == "fallback"
    assert event.sub_queries[0].shape == "semantic"
    assert ran_tools(ran.events) == {"search", "soft"}


async def test_planner_down_still_answers_by_meaning() -> None:
    w = await world()
    ran = await run_recall(
        w, "Where do I live?", fake(("plan", FakeOutcome(error=ProviderErrorKind.SERVER)))
    )
    event = ran.events.of("retrieval")[0]
    assert event.plan_source == "fallback"
    assert "unavailable" in event.plan_note


async def test_unknown_vocab_is_dropped_not_guessed() -> None:
    w = await world()
    ran = await run_recall(
        w,
        "What hikes did I do?",
        fake(
            (
                "plan",
                plan(
                    {
                        "question": "What hikes did I do?",
                        "shape": "list",
                        "filters": {"kinds": ["episode"], "subtypes": ["hiking"]},
                    }
                ),
            )
        ),
    )
    trace = ran.events.of("retrieval")[0].sub_queries[0]
    assert any("hiking" in d for d in trace.dropped)
    (call, *_) = ran.store.of("lookup")
    assert call.args[0].subtypes == ()


async def test_a_relation_path_is_followed_to_the_right_person() -> None:
    w = await world()
    ran = await run_recall(
        w,
        "What does Nisha's husband like?",
        fake(
            (
                "plan",
                plan(
                    {
                        "question": "What does Nisha's husband like?",
                        "shape": "entity",
                        "entities": [
                            {"mention": "Nisha's husband", "name": "Nisha", "path": ["spouse_of"]}
                        ],
                    }
                ),
            )
        ),
    )
    entity = ran.events.of("retrieval")[0].sub_queries[0].entities[0]
    assert entity.outcome == "matched"
    assert entity.names == ["Rohan"]
    call = ran.store.of("entity")[0]
    assert call.args[0] == [w.ids["nisha"]]
    assert [h.relation for h in call.kwargs["path"]] == ["spouse_of"]


async def test_no_evidence_answers_from_a_template_without_the_answer_model() -> None:
    w = await world()
    ran = await run_recall(
        w,
        "Anything about sourdough?",
        fake(("plan", plan({"question": "Anything about sourdough?", "shape": "semantic"}))),
    )
    assert ran.reply.startswith("I don't have anything saved about")
    assert "answer" not in [c.step for c in ran.calls]
    assert ran.outcome.answered_by_model is False
    assert ran.events.of("citations")[0].citations == []


def _found(*ids: Any) -> Any:
    return lambda *a, **k: [Hit(item_id=i, rank=n + 1) for n, i in enumerate(ids)]


async def test_citations_map_to_memories_and_bad_markers_are_stripped() -> None:
    w = await world()
    store = RecordingStore(
        results={"lookup": LookupResult([Hit(w.ids["pune"], 1)], 1), "search": _found()}
    )
    ran = await run_recall(
        w,
        "Where do I live?",
        fake(
            ("plan", plan({"question": "Where do I live?", "shape": "exact", "filters": LIVES})),
            ("answer", FakeOutcome(text="You live in Pune [1][7].")),
        ),
        store,
    )
    assert ran.reply == "You live in Pune [1]."
    cites = ran.events.of("citations")[0]
    assert [(c.marker, c.item_id) for c in cites.citations] == [(1, w.ids["pune"])]
    assert cites.stripped == 1
    assert ran.outcome.cited == [w.ids["pune"]]


async def test_the_pack_carries_durations_computed_in_code() -> None:
    w = await world()
    provider = fake(("plan", plan({"question": "When did I last run?", "shape": "semantic"})))
    store = RecordingStore(results={"search": _found(w.ids["run"])})
    await run_recall(
        w, "When did I last run?", provider, store, settings=RecallSettings(rerank_enabled=False)
    )
    (answer,) = [r for r in provider.requests if r.step == "answer"]
    assert "on Wednesday 2 September 2026 (5 weeks ago, past)" in (answer.system or "")


async def test_a_multi_part_question_says_which_part_found_nothing() -> None:
    w = await world()
    store = RecordingStore(results={"lookup": LookupResult([Hit(w.ids["pune"], 1)], 1)})
    ran = await run_recall(
        w,
        "Where do I live and what do I know about sourdough?",
        fake(
            (
                "plan",
                plan(
                    {"question": "Where do I live?", "shape": "exact", "filters": LIVES},
                    {"question": "What do I know about sourdough?", "shape": "semantic"},
                ),
            )
        ),
        store,
    )
    assert "I live in Pune" in ran.reply
    assert "I don't have anything saved about" in ran.reply
    assert len(ran.events.of("retrieval")[0].sub_queries) == 2


async def test_relaxation_loosens_one_filter_at_a_time_and_stops_at_the_first_hit() -> None:
    w = await world()

    def lookup(filters: Filters, *a: Any, **k: Any) -> LookupResult:
        return (
            LookupResult([Hit(w.ids["run"], 1)], 1) if not filters.subtypes else LookupResult([], 0)
        )

    store = RecordingStore(results={"lookup": lookup})
    ran = await run_recall(
        w,
        "Which runs did I log?",
        fake(
            (
                "plan",
                plan(
                    {
                        "question": "Which runs did I log?",
                        "shape": "list",
                        "filters": {
                            "kinds": ["episode"],
                            "subtypes": ["measurement"],
                            "category": "health",
                        },
                    }
                ),
            )
        ),
        store,
    )
    trace = ran.events.of("retrieval")[0].sub_queries[0]
    steps = [(r.step, r.count) for r in trace.relaxation]
    assert steps[-1] == ("subtype", 1)
    assert all(count == 0 for _, count in steps[:-1])
    assert [s for s, _ in steps] == ["category", "subtype"][-len(steps) :]


async def test_count_is_never_relaxed() -> None:
    w = await world()
    ran = await run_recall(
        w,
        "How many runs in September?",
        fake(
            (
                "plan",
                plan(
                    {
                        "question": "How many runs in September?",
                        "shape": "count",
                        "filters": {"subtypes": ["measurement"]},
                        "aggregate": {"op": "count"},
                        "times": [{"expression": "in September", "clock": "occurred"}],
                    }
                ),
            )
        ),
    )
    assert ran.events.of("retrieval")[0].sub_queries[0].relaxation == []
    assert len(ran.store.of("aggregate")) == 1


async def test_a_failed_tool_never_fails_the_turn() -> None:
    w = await world()
    store = RecordingStore(fail={"lookup"}, results={"search": _found(w.ids["pune"])})
    ran = await run_recall(
        w,
        "Where do I live?",
        fake(("plan", plan({"question": "Where do I live?", "shape": "exact", "filters": LIVES}))),
        store,
    )
    runs = ran.events.of("retrieval")[0].sub_queries[0].tools
    assert any(r.tool == "lookup" and r.error for r in runs)
    assert "Pune" in ran.reply


async def test_fusion_rewards_agreement_and_demotes_history() -> None:
    w = await world()
    items = {i.id: i for i in await w.memory.reader(w.scope).items(list(w.ids.values()))}
    pune, jazz = w.ids["pune"], w.ids["jazz"]
    fused = fuse(
        {"lookup": [Hit(jazz, 1), Hit(pune, 2)], "soft": [Hit(pune, 1)]},
        items,
        shape=Shape.EXACT,
        rrf_k=60,
        history_demotion=0.5,
    )
    assert [c.item_id for c in fused] == [pune, jazz]

    items[pune] = items[pune].model_copy(update={"valid_to": datetime(2026, 9, 1, tzinfo=UTC)})
    channels = {"lookup": [Hit(pune, 1), Hit(jazz, 2)]}
    demoted = fuse(channels, items, shape=Shape.EXACT, rrf_k=60, history_demotion=0.5)
    assert [(c.item_id, c.demoted) for c in demoted] == [(jazz, False), (pune, True)]
    kept = fuse(channels, items, shape=Shape.HISTORY, rrf_k=60, history_demotion=0.5)
    assert [c.item_id for c in kept] == [pune, jazz]

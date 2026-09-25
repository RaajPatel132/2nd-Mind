"""S2.14: the verbal renderer, golden strings per kind and per state change. First person,
absolute calendar words only, names with labels, kind and state in plain words, units written
out. All data is synthetic; times are local to Asia/Kolkata."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from secondmind.core import EntityRole, Kind, ResourceFormat, TimePrecision, initial_state
from secondmind.memory import ItemContent, has_relative_time
from secondmind.memory.render import RenderEntity, RenderInput, render_change, render_verbal

TZ = "Asia/Kolkata"


def at(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=ZoneInfo(TZ)).astimezone(UTC)


M = at(2026, 9, 18, 10)


def item(
    kind: Kind, text: str, title: str | None = None, state: str | None = None, **kw: object
) -> ItemContent:
    mentioned = kw.pop("mentioned_at", M)
    return ItemContent(
        kind=kind,
        state=state or initial_state(kind),
        text=text,
        title=title or text,
        mentioned_at=mentioned,  # type: ignore[arg-type]
        **kw,  # type: ignore[arg-type]
    )


nisha = RenderEntity("Nisha", EntityRole.BY, label="sister")
sev = RenderEntity("Severance season 2", EntityRole.ABOUT, kind="work", type_label="TV series")
cubbon = RenderEntity("Cubbon Park", EntityRole.AT, kind="place")

CASES: dict[str, RenderInput] = {
    "episode": RenderInput(
        item(
            Kind.EPISODE,
            "I ran 5 km in 31 min",
            "Morning run",
            subtype="measurement",
            attributes={"activity": "run"},
            occurred_start=at(2026, 9, 27, 7),
            time_precision=TimePrecision.DATETIME,
        ),
        TZ,
        [cubbon],
        category="health/exercise",
    ),
    "intention": RenderInput(
        item(Kind.INTENTION, "Watch Severance season 2", "Severance season 2", subtype="watch"),
        TZ,
        [sev, nisha],
    ),
    "intention_fulfilled": RenderInput(
        item(
            Kind.INTENTION,
            "Watch Severance season 2",
            "Severance season 2",
            subtype="watch",
            state="fulfilled",
        ),
        TZ,
        [sev, nisha],
        fulfilled_by=item(
            Kind.EPISODE,
            "Watched Severance",
            occurred_start=at(2026, 9, 22, 21),
            time_precision=TimePrecision.DATETIME,
        ),
    ),
    "plan": RenderInput(
        item(
            Kind.PLAN,
            "Dentist appointment",
            occurred_start=at(2026, 10, 3, 17),
            time_precision=TimePrecision.DATETIME,
        ),
        TZ,
        [],
    ),
    "plan_moved": RenderInput(
        item(
            Kind.PLAN,
            "Dentist appointment",
            state="moved",
            occurred_start=at(2026, 10, 2, 17),
            time_precision=TimePrecision.DATETIME,
        ),
        TZ,
        [],
        superseded_by=item(
            Kind.PLAN,
            "Dentist appointment",
            occurred_start=at(2026, 10, 3, 17),
            time_precision=TimePrecision.DATETIME,
        ),
    ),
    "plan_change_key": RenderInput(
        item(
            Kind.PLAN,
            "Dentist appointment",
            occurred_start=at(2026, 10, 3, 17),
            time_precision=TimePrecision.DATETIME,
            mentioned_at=at(2026, 9, 20, 10),
        ),
        TZ,
        [],
        replaced=item(
            Kind.PLAN,
            "Dentist appointment",
            occurred_start=at(2026, 10, 2, 17),
            time_precision=TimePrecision.DATETIME,
        ),
    ),
    "routine": RenderInput(
        item(
            Kind.PLAN,
            "Gym",
            rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR;BYHOUR=7",
            occurred_start=at(2026, 9, 21, 7),
            time_precision=TimePrecision.DATETIME,
        ),
        TZ,
        [],
    ),
    "task": RenderInput(
        item(
            Kind.TASK, "Call the plumber", due_at=at(2026, 9, 25), time_precision=TimePrecision.DAY
        ),
        TZ,
        [],
    ),
    "task_done": RenderInput(
        item(
            Kind.TASK,
            "Call the plumber",
            state="done",
            due_at=at(2026, 9, 25),
            time_precision=TimePrecision.DAY,
        ),
        TZ,
        [],
    ),
    "fact": RenderInput(item(Kind.FACT, "I live in Pune"), TZ, []),
    "fact_superseded": RenderInput(
        item(Kind.FACT, "I live in Bengaluru", state="superseded", valid_to=at(2026, 9, 12, 10)),
        TZ,
        [],
        superseded_by=item(Kind.FACT, "I live in Pune"),
    ),
    "fact_change_key": RenderInput(
        item(Kind.FACT, "I live in Pune", mentioned_at=at(2026, 9, 12, 10)),
        TZ,
        [],
        replaced=item(Kind.FACT, "I live in Bengaluru"),
    ),
    "preference": RenderInput(
        item(Kind.PREFERENCE, "Kabir likes fountain pens"),
        TZ,
        [RenderEntity("Kabir", EntityRole.ABOUT, label="partner")],
    ),
    "resource": RenderInput(
        item(Kind.RESOURCE, "An article on sleep", "Why we sleep", format=ResourceFormat.ARTICLE),
        TZ,
        [],
    ),
    "resource_consumed": RenderInput(
        item(
            Kind.RESOURCE,
            "An article on sleep",
            "Why we sleep",
            format=ResourceFormat.ARTICLE,
            state="consumed",
        ),
        TZ,
        [],
    ),
    "note": RenderInput(
        item(Kind.NOTE, "Trading idea: buy the dip on rainy days", subtype="idea"),
        TZ,
        [],
        category="finance/ideas",
    ),
    "rule": RenderInput(
        item(Kind.RULE, "Always answer in British English", state="active"), TZ, []
    ),
    "pattern": RenderInput(
        item(Kind.PATTERN, "I run on weekend mornings", state="confirmed"), TZ, []
    ),
}

EXPECTED = {
    "episode": (
        "Logged run: I ran 5 km in 31 minutes on Sunday 27 September 2026, in the morning, at "
        "Cubbon Park. Weekend, September 2026, exercise."
    ),
    "intention": (
        "Wish list, to watch: Severance season 2 (TV series), recommended by Nisha (sister). "
        "Added Friday 18 September 2026. Not watched yet."
    ),
    "intention_fulfilled": (
        "Watched Severance season 2 (TV series) on Tuesday 22 September 2026, at night. It was "
        "on my to-watch list from 18 September 2026, recommended by Nisha (sister)."
    ),
    "plan": (
        "Upcoming: Dentist appointment on Saturday 3 October 2026, 5 pm. Weekend, October 2026."
    ),
    "plan_moved": (
        "Moved: Dentist appointment was on Friday 2 October 2026, 5 pm; moved to Saturday 3 "
        "October 2026, 5 pm."
    ),
    "plan_change_key": (
        "Changed on 20 September 2026: dentist appointment moved from Friday 2 October to "
        "Saturday 3 October 2026, 5 pm."
    ),
    "routine": "Routine: gym every Monday, Wednesday and Friday at 7 am, since September 2026.",
    "task": "To do: Call the plumber, due Friday 25 September 2026. Open.",
    "task_done": "Done: Call the plumber, was due Friday 25 September 2026.",
    "fact": "Fact: I live in Pune. Noted Friday 18 September 2026.",
    "fact_superseded": (
        "No longer true since 12 September 2026: I lived in Bengaluru. I live in Pune now."
    ),
    "fact_change_key": (
        "Changed on 12 September 2026: I live in Pune; before that, I lived in Bengaluru."
    ),
    "preference": (
        "Preference: Kabir likes fountain pens. About Kabir (partner). Noted Friday 18 "
        "September 2026."
    ),
    "resource": "Saved article: Why we sleep. Saved Friday 18 September 2026. Not read yet.",
    "resource_consumed": "Read article: Why we sleep. Saved Friday 18 September 2026.",
    "note": (
        "Note (idea): Trading idea: buy the dip on rainy days. Noted Friday 18 September 2026. "
        "Ideas."
    ),
    "rule": "Rule for the assistant (active): Always answer in British English.",
    "pattern": "Pattern (confirmed): I run on weekend mornings.",
}


@pytest.mark.parametrize("name", list(EXPECTED))
def test_golden_sentence(name: str) -> None:
    render = render_change if name.endswith("change_key") else render_verbal
    assert render(CASES[name]) == EXPECTED[name]


def test_every_kind_has_a_golden_sentence() -> None:
    kinds = {CASES[name].item.kind for name in EXPECTED}
    assert kinds == set(Kind)


@pytest.mark.parametrize("name", list(EXPECTED))
def test_no_sentence_uses_relative_time(name: str) -> None:
    assert not has_relative_time(EXPECTED[name])


def test_a_change_key_needs_a_replaced_item() -> None:
    assert render_change(CASES["fact"]) is None

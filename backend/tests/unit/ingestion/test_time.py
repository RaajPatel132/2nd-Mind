"""S2.5: the deterministic time resolver. The model never does date arithmetic.

Unless a case says otherwise, now = Wednesday 2026-09-23 10:00 Asia/Kolkata (§8.1).
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from secondmind.core import TimeClock, TimePrecision
from secondmind.ingestion import (
    Resolved,
    TurnNow,
    UnresolvableTimeError,
    reminder_time,
    resolve,
    resolve_lead,
    validate_rrule,
)

KOLKATA = "Asia/Kolkata"
NOW = TurnNow(datetime(2026, 9, 23, 4, 30, tzinfo=UTC), KOLKATA)
OCC, V, D = TimeClock.OCCURRED, TimeClock.VALID, TimeClock.DUE


def at(local: str, tz: str = KOLKATA) -> TurnNow:
    return TurnNow(datetime.fromisoformat(local).replace(tzinfo=ZoneInfo(tz)).astimezone(UTC), tz)


def show(r: Resolved, tz: str = KOLKATA) -> str:
    local = r.start.astimezone(ZoneInfo(tz))
    match r.precision:
        case TimePrecision.DATETIME:
            return local.strftime("%Y-%m-%dT%H:%M")
        case TimePrecision.MONTH:
            return local.strftime("%Y-%m")
        case TimePrecision.YEAR:
            return local.strftime("%Y")
        case _:
            return local.date().isoformat()


# (expression, clock, direction, expected value, precision, assumed)
BASICS: list[tuple[str, TimeClock, str, str, TimePrecision, bool]] = [
    ("today", OCC, "future", "2026-09-23", TimePrecision.DAY, False),
    ("tomorrow", OCC, "future", "2026-09-24", TimePrecision.DAY, False),
    ("yesterday", OCC, "past", "2026-09-22", TimePrecision.DAY, False),
    ("the day after tomorrow", OCC, "future", "2026-09-25", TimePrecision.DAY, False),
    ("tonight", OCC, "future", "2026-09-23T21:00", TimePrecision.DATETIME, False),
    ("tomorrow night", OCC, "future", "2026-09-24T21:00", TimePrecision.DATETIME, False),
    ("this morning", OCC, "past", "2026-09-23T08:00", TimePrecision.DATETIME, False),
    ("last night", OCC, "past", "2026-09-22T21:00", TimePrecision.DATETIME, False),
    # bare and qualified weekdays
    ("Friday", OCC, "future", "2026-09-25", TimePrecision.DAY, False),
    ("on Friday", OCC, "future", "2026-09-25", TimePrecision.DAY, False),
    ("this Friday", OCC, "future", "2026-09-25", TimePrecision.DAY, False),
    ("next Friday", OCC, "future", "2026-10-02", TimePrecision.DAY, True),
    ("next Monday", OCC, "future", "2026-09-28", TimePrecision.DAY, False),
    ("last Saturday", OCC, "past", "2026-09-19", TimePrecision.DAY, False),
    ("Saturday", OCC, "past", "2026-09-19", TimePrecision.DAY, False),
    ("on Sunday", OCC, "past", "2026-09-20", TimePrecision.DAY, False),
    ("Wednesday", OCC, "future", "2026-09-30", TimePrecision.DAY, True),
    ("Wednesday", OCC, "past", "2026-09-16", TimePrecision.DAY, True),
    ("this Wednesday", OCC, "future", "2026-09-23", TimePrecision.DAY, False),
    ("next Wednesday", OCC, "future", "2026-09-30", TimePrecision.DAY, False),
    # days of the month
    ("the 3rd of next month", OCC, "future", "2026-10-03", TimePrecision.DAY, False),
    ("the 15th", OCC, "future", "2026-10-15", TimePrecision.DAY, False),
    ("the 28th", OCC, "future", "2026-09-28", TimePrecision.DAY, False),
    ("the 31st", OCC, "future", "2026-09-30", TimePrecision.DAY, True),
    ("14 March", OCC, "future", "2027-03-14", TimePrecision.DAY, False),
    ("March 14", OCC, "past", "2026-03-14", TimePrecision.DAY, False),
    ("3rd October 2026", OCC, "future", "2026-10-03", TimePrecision.DAY, False),
    # months and years: precision is honest
    ("next May", OCC, "future", "2027-05", TimePrecision.MONTH, False),
    ("in May", OCC, "future", "2027-05", TimePrecision.MONTH, False),
    ("May", OCC, "past", "2026-05", TimePrecision.MONTH, False),
    ("May 2027", OCC, "future", "2027-05", TimePrecision.MONTH, False),
    ("next month", OCC, "future", "2026-10", TimePrecision.MONTH, False),
    ("last month", OCC, "past", "2026-08", TimePrecision.MONTH, False),
    ("next year", OCC, "future", "2027", TimePrecision.YEAR, False),
    ("last year", OCC, "past", "2025", TimePrecision.YEAR, False),
    ("2027", OCC, "future", "2027", TimePrecision.YEAR, False),
    # in N units / ago
    ("in 3 days", OCC, "future", "2026-09-26", TimePrecision.DAY, False),
    ("in two weeks", OCC, "future", "2026-10-07", TimePrecision.DAY, False),
    ("in a month", OCC, "future", "2026-10-23", TimePrecision.DAY, False),
    ("a week from today", OCC, "future", "2026-09-30", TimePrecision.DAY, False),
    ("3 days ago", OCC, "past", "2026-09-20", TimePrecision.DAY, False),
    ("in 2 hours", OCC, "future", "2026-09-23T12:00", TimePrecision.DATETIME, False),
    # ISO and numeric
    ("2026-10-03", OCC, "future", "2026-10-03", TimePrecision.DAY, False),
    ("2026-10-03T17:30", OCC, "future", "2026-10-03T17:30", TimePrecision.DATETIME, False),
    ("03/10/2026", OCC, "future", "2026-10-03", TimePrecision.DAY, True),
    ("25/12", OCC, "future", "2026-12-25", TimePrecision.DAY, False),
    # times of day
    ("3 October at 5 pm", OCC, "future", "2026-10-03T17:00", TimePrecision.DATETIME, False),
    ("Friday at 7", OCC, "future", "2026-09-25T07:00", TimePrecision.DATETIME, True),
    ("tomorrow at 5", OCC, "future", "2026-09-24T17:00", TimePrecision.DATETIME, True),
    ("tomorrow 7:30 pm", OCC, "future", "2026-09-24T19:30", TimePrecision.DATETIME, False),
    ("at noon", OCC, "future", "2026-09-23T12:00", TimePrecision.DATETIME, False),
    ("at 9am", OCC, "future", "2026-09-24T09:00", TimePrecision.DATETIME, False),
    ("Friday evening", OCC, "future", "2026-09-25T18:00", TimePrecision.DATETIME, False),
    # periods and edges
    ("this weekend", OCC, "future", "2026-09-26", TimePrecision.DAY, False),
    ("next week", OCC, "future", "2026-09-28", TimePrecision.DAY, False),
    ("last week", OCC, "past", "2026-09-14", TimePrecision.DAY, False),
    ("end of the month", D, "future", "2026-09-30", TimePrecision.DAY, False),
    ("end of next month", D, "future", "2026-10-31", TimePrecision.DAY, False),
    ("end of the year", D, "future", "2026-12-31", TimePrecision.DAY, False),
    ("end of the week", D, "future", "2026-09-27", TimePrecision.DAY, True),
    # past episodes
    ("last Saturday morning", OCC, "past", "2026-09-19T08:00", TimePrecision.DATETIME, False),
    ("on Monday", OCC, "past", "2026-09-21", TimePrecision.DAY, False),
    # due dates
    ("by Friday", D, "auto", "2026-09-25", TimePrecision.DAY, False),
    ("before the 1st", D, "auto", "2026-10-01", TimePrecision.DAY, False),
]


@pytest.mark.parametrize(
    ("expression", "clock", "direction", "value", "precision", "assumed"),
    BASICS,
    ids=[f"{c[0]}-{c[2]}" for c in BASICS],
)
def test_basics(  # noqa: PLR0917
    expression: str,
    clock: TimeClock,
    direction: str,
    value: str,
    precision: TimePrecision,
    assumed: bool,
) -> None:
    r = resolve(expression, clock, NOW, direction=direction)  # type: ignore[arg-type]
    assert (show(r), r.precision, r.assumed) == (value, precision, assumed), r.rule
    assert r.expression == expression
    assert r.clock is clock
    assert r.rule
    if r.assumed:
        assert r.alternative


def test_the_31st_in_a_30_day_month_names_the_alternative() -> None:
    r = resolve("the 31st", OCC, NOW, direction="future")
    assert r.alternative == "31 October (September has 30 days)"


def test_the_31st_of_a_30_day_next_month_is_clamped_and_assumed() -> None:
    r = resolve("the 31st of next month", OCC, at("2026-10-05T10:00"), direction="future")
    assert (show(r), r.assumed, r.alternative) == ("2026-11-30", True, "November has 30 days")


def test_year_rollover_next_january_in_december() -> None:
    r = resolve("next January", OCC, at("2026-12-10T10:00"), direction="future")
    assert (show(r), r.precision) == ("2027-01", TimePrecision.MONTH)


def test_next_may_before_may_is_this_years_but_says_so() -> None:
    r = resolve("next May", OCC, at("2026-02-10T10:00"), direction="future")
    assert (show(r), r.assumed, r.alternative) == ("2026-05", True, "May 2027")


def test_a_month_window_ends_at_the_next_month() -> None:
    r = resolve("next May", OCC, NOW, direction="future")
    assert r.end is not None
    assert r.end.astimezone(ZoneInfo(KOLKATA)).date().isoformat() == "2027-06-01"


def test_a_week_is_a_seven_day_window() -> None:
    r = resolve("last week", OCC, NOW, direction="past")
    assert r.end is not None
    assert (r.end - r.start) == timedelta(days=7)


def test_numeric_dates_follow_the_timezone_convention() -> None:
    r = resolve("10/03/2026", OCC, at("2026-09-23T10:00", "America/New_York"), direction="future")
    assert (show(r, "America/New_York"), r.rule, r.assumed) == (
        "2026-10-03",
        "numeric_date_mdy",
        True,
    )


# ------------------------------------------------------------------ timezones


def test_dst_observing_timezone_across_the_november_switch() -> None:
    ny = at("2026-10-31T12:00", "America/New_York")  # EDT, UTC-4
    before = resolve("today at 9am", OCC, ny, direction="past")
    after = resolve("tomorrow at 9am", OCC, ny, direction="future")
    assert before.start == datetime(2026, 10, 31, 13, 0, tzinfo=UTC)  # 9:00 EDT
    assert after.start == datetime(2026, 11, 1, 14, 0, tzinfo=UTC)  # 9:00 EST
    in_two = resolve("in 2 days", OCC, ny, direction="future")
    assert in_two.start == datetime(2026, 11, 2, 5, 0, tzinfo=UTC)  # local midnight, EST


def test_local_date_differs_from_the_utc_date() -> None:
    late = TurnNow(datetime(2026, 9, 23, 20, 0, tzinfo=UTC), KOLKATA)  # 01:30 on the 24th
    assert show(resolve("today", OCC, late)) == "2026-09-24"
    assert show(resolve("tomorrow", OCC, late)) == "2026-09-25"
    auckland = TurnNow(datetime(2026, 9, 23, 13, 0, tzinfo=UTC), "Pacific/Auckland")
    assert show(resolve("yesterday", OCC, auckland, direction="past"), "Pacific/Auckland") == (
        "2026-09-23"
    )


def test_day_values_are_local_midnight_as_utc() -> None:
    r = resolve("the 3rd of next month", OCC, NOW)
    assert r.start == datetime(2026, 10, 2, 18, 30, tzinfo=UTC)


# ------------------------------------------------------------------ routines -> RRULE

ROUTINES = [
    (
        "every Mon/Wed/Fri at 7",
        "FREQ=WEEKLY;BYDAY=MO,WE,FR;BYHOUR=7;BYMINUTE=0",
        "2026-09-25T07:00",
        True,
    ),
    ("every other Sunday", "FREQ=WEEKLY;BYDAY=SU;INTERVAL=2", "2026-09-27", False),
    ("first Monday of the month", "FREQ=MONTHLY;BYDAY=1MO", "2026-10-05", False),
    ("last Friday of the month", "FREQ=MONTHLY;BYDAY=-1FR", "2026-09-25", False),
    ("every 14 March", "FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=14", "2027-03-14", False),
    ("every year on 14 March", "FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=14", "2027-03-14", False),
    (
        "every weekday at 8:30 am",
        "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=8;BYMINUTE=30",
        "2026-09-24T08:30",
        False,
    ),
    ("daily at 9pm", "FREQ=DAILY;BYHOUR=21;BYMINUTE=0", "2026-09-23T21:00", False),
    ("every month on the 5th", "FREQ=MONTHLY;BYMONTHDAY=5", "2026-10-05", False),
    ("mondays", "FREQ=WEEKLY;BYDAY=MO", "2026-09-28", False),
    ("every weekend", "FREQ=WEEKLY;BYDAY=SA,SU", "2026-09-26", False),
]


@pytest.mark.parametrize(
    ("expression", "rrule", "first", "assumed"), ROUTINES, ids=[r[0] for r in ROUTINES]
)
def test_routines_become_rrules(expression: str, rrule: str, first: str, assumed: bool) -> None:
    r = resolve(expression, OCC, NOW)
    assert (r.rrule, show(r), r.assumed) == (rrule, first, assumed)
    assert r.rule.startswith("routine")
    validate_rrule(rrule, r.start)


def test_a_recurring_date_becomes_yearly() -> None:
    r = resolve("14 March", OCC, NOW, recurring=True)
    assert (r.rrule, show(r)) == ("FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=14", "2027-03-14")


def test_invalid_rrules_are_rejected_on_write() -> None:
    with pytest.raises(ValueError, match="FREQ"):
        validate_rrule("FREQ=SOMETIMES", NOW.instant)


# ------------------------------------------------------------------ validity phrases


def test_since_a_month_is_valid_from_its_start() -> None:
    r = resolve("since March", V, NOW)
    assert (show(r), r.precision, r.bound) == ("2026-03", TimePrecision.MONTH, "since")


def test_since_a_year() -> None:
    r = resolve("since 2019", V, NOW)
    assert (show(r), r.precision) == ("2019", TimePrecision.YEAR)


def test_since_last_monday() -> None:
    assert show(resolve("since last Monday", V, NOW)) == "2026-09-21"


def test_until_last_week_is_a_past_window() -> None:
    r = resolve("until last week", V, NOW)
    assert (show(r), r.bound) == ("2026-09-14", "until")


def test_until_a_month_defaults_to_the_future() -> None:
    assert show(resolve("until March", V, NOW)) == "2027-03"


def test_until_a_month_in_the_past_when_told() -> None:
    assert show(resolve("until March", V, NOW, direction="past")) == "2026-03"


def test_unresolvable_expressions_raise() -> None:
    with pytest.raises(UnresolvableTimeError):
        resolve("whenever I get round to it", OCC, NOW)


# ------------------------------------------------------------------ reminders (time triggers)


@pytest.mark.parametrize(
    ("expression", "delta"),
    [
        ("2 hours before", timedelta(hours=2)),
        ("the day before", timedelta(days=1)),
        ("a week before", timedelta(weeks=1)),
        ("30 minutes before", timedelta(minutes=30)),
        ("half an hour before", timedelta(minutes=30)),
    ],
)
def test_lead_times(expression: str, delta: timedelta) -> None:
    assert resolve_lead(expression).delta == delta


def test_passport_example_reminder_fires_the_day_before() -> None:
    """§8.1 (a): a plan on 2026-10-03 at day precision; the trigger fires on 2026-10-02."""
    event = resolve("the 3rd of next month", OCC, NOW)
    fires = reminder_time(event, event.precision, timedelta(days=1), KOLKATA)
    assert (show(event), event.precision) == ("2026-10-03", TimePrecision.DAY)
    assert fires.astimezone(ZoneInfo(KOLKATA)).strftime("%Y-%m-%d %H:%M") == "2026-10-02 09:00"


def test_a_timed_event_is_reminded_the_lead_before() -> None:
    event = resolve("Friday at 7:00 pm", OCC, NOW)
    fires = reminder_time(event, event.precision, timedelta(hours=2), KOLKATA)
    assert fires.astimezone(ZoneInfo(KOLKATA)).strftime("%Y-%m-%d %H:%M") == "2026-09-25 17:00"


def test_a_weekday_with_a_date_is_checked_against_it() -> None:
    ok = resolve("Friday 2 October at 5pm", OCC, NOW, direction="future")
    assert (show(ok), ok.assumed) == ("2026-10-02T17:00", False)
    wrong = resolve("Friday 3 October", OCC, NOW, direction="future")  # the 3rd is a Saturday
    assert (show(wrong), wrong.assumed, wrong.alternative) == (
        "2026-10-03",
        True,
        "Friday 2 October",
    )


def test_words_it_cant_read_never_default_to_today() -> None:
    with pytest.raises(UnresolvableTimeError):
        resolve("the Tuesday after the festival at 5pm", OCC, NOW)

"""S3.4: time expressions in questions become [start, end) windows through S2's resolver. The
model never computes a date: it hands over the expression, a clock and a direction.

Unless a case says otherwise, now = Tuesday 2026-10-06 10:00 Asia/Kolkata (the recall fixture).
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from secondmind.core import TimeClock, TimePrecision
from secondmind.ingestion import TurnNow, UnresolvableTimeError, Window, resolve_window

KOLKATA = "Asia/Kolkata"
OCC = TimeClock.OCCURRED


def at(local: str, tz: str = KOLKATA) -> TurnNow:
    return TurnNow(datetime.fromisoformat(local).replace(tzinfo=ZoneInfo(tz)).astimezone(UTC), tz)


NOW = at("2026-10-06T10:00")  # a Tuesday


def span(w: Window, tz: str = KOLKATA) -> tuple[str | None, str | None]:
    def show(d: datetime | None) -> str | None:
        return None if d is None else d.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%dT%H:%M")

    return show(w.start), show(w.end)


def window(expr: str, now: TurnNow = NOW, **kw: object) -> Window:
    return resolve_window(expr, OCC, now, **kw)  # type: ignore[arg-type]


# ------------------------------------------------------------------ weeks (Monday first)


def test_this_week_runs_monday_to_monday() -> None:
    assert span(window("this week")) == ("2026-10-05T00:00", "2026-10-12T00:00")


def test_next_week() -> None:
    assert span(window("next week")) == ("2026-10-12T00:00", "2026-10-19T00:00")


def test_last_week() -> None:
    w = window("last week", direction="past")
    assert span(w) == ("2026-09-28T00:00", "2026-10-05T00:00")
    assert w.rule == "last_week"


def test_coming_up_this_week_starts_now() -> None:
    assert span(window("this week", direction="future")) == ("2026-10-06T10:00", "2026-10-12T00:00")


# ------------------------------------------------------------------ weekends


def test_this_weekend_asked_on_a_tuesday_is_the_coming_one() -> None:
    assert span(window("this weekend")) == ("2026-10-10T00:00", "2026-10-12T00:00")


def test_this_weekend_asked_on_a_saturday_is_today_and_tomorrow() -> None:
    saturday = at("2026-10-10T09:00")
    assert span(window("this weekend", saturday)) == ("2026-10-10T00:00", "2026-10-12T00:00")


def test_this_weekend_looking_ahead_on_a_saturday_starts_now() -> None:
    saturday = at("2026-10-10T09:00")
    w = window("this weekend", saturday, direction="future")
    assert span(w) == ("2026-10-10T09:00", "2026-10-12T00:00")


# ------------------------------------------------------------------ months and years


def test_last_month_is_the_whole_of_september() -> None:
    w = window("last month")
    assert span(w) == ("2026-09-01T00:00", "2026-10-01T00:00")
    assert w.precision is TimePrecision.MONTH


def test_in_may_looking_back_is_this_years_may() -> None:
    assert span(window("in May", direction="past")) == ("2026-05-01T00:00", "2026-06-01T00:00")


def test_in_may_looking_forward_is_next_years_may() -> None:
    assert span(window("in May", direction="future")) == ("2027-05-01T00:00", "2027-06-01T00:00")


def test_september_asked_in_september_looking_back_ends_now() -> None:
    now = at("2026-09-26T18:00")
    assert span(window("September", now, direction="past")) == (
        "2026-09-01T00:00",
        "2026-09-26T18:00",
    )


def test_a_year() -> None:
    w = window("2025")
    assert span(w) == ("2025-01-01T00:00", "2026-01-01T00:00")
    assert w.precision is TimePrecision.YEAR


def test_year_rollover_last_month_in_january_is_december() -> None:
    january = at("2027-01-15T12:00")
    assert span(window("last month", january)) == ("2026-12-01T00:00", "2027-01-01T00:00")


def test_year_rollover_next_month_in_december_is_january() -> None:
    december = at("2026-12-20T12:00")
    assert span(window("next month", december)) == ("2027-01-01T00:00", "2027-02-01T00:00")


# ------------------------------------------------------------------ days and parts of days


def test_yesterday() -> None:
    assert span(window("yesterday")) == ("2026-10-05T00:00", "2026-10-06T00:00")


def test_this_morning() -> None:
    w = window("this morning")
    assert span(w) == ("2026-10-06T05:00", "2026-10-06T12:00")
    assert w.precision is TimePrecision.DATETIME


def test_a_date_with_a_month() -> None:
    assert span(window("12 September", direction="past")) == (
        "2026-09-12T00:00",
        "2026-09-13T00:00",
    )


# ------------------------------------------------------------------ counted from now


def test_the_last_seven_days() -> None:
    assert span(window("the last 7 days")) == ("2026-09-29T10:00", "2026-10-06T10:00")


def test_the_past_two_weeks() -> None:
    assert span(window("past two weeks")) == ("2026-09-22T10:00", "2026-10-06T10:00")


def test_the_next_thirty_days() -> None:
    assert span(window("the next 30 days")) == ("2026-10-06T10:00", "2026-11-05T10:00")


# ------------------------------------------------------------------ bounds


def test_since_a_date_runs_to_now() -> None:
    w = window("since 12 September", direction="past")
    assert span(w) == ("2026-09-12T00:00", "2026-10-06T10:00")
    assert w.rule.startswith("since")


def test_between_two_dates_includes_the_last_day() -> None:
    w = window("between 1 and 7 September", direction="past")
    assert span(w) == ("2026-09-01T00:00", "2026-09-08T00:00")
    assert w.rule == "between"


def test_before_a_month_is_open_at_the_start() -> None:
    assert span(window("before September", direction="past")) == (None, "2026-09-01T00:00")


def test_after_a_date_is_open_at_the_end_unless_looking_back() -> None:
    assert span(window("after 3 October")) == ("2026-10-04T00:00", None)
    assert span(window("after 3 October", direction="past")) == (
        "2026-10-04T00:00",
        "2026-10-06T10:00",
    )


def test_until_friday_looking_ahead_starts_now() -> None:
    assert span(window("until Friday", direction="future")) == (
        "2026-10-06T10:00",
        "2026-10-10T00:00",
    )


def test_ever_is_open_on_both_sides() -> None:
    assert span(window("ever")) == (None, None)


# ------------------------------------------------------------------ anchors: before Goa

GOA = (
    datetime(2026, 9, 25, tzinfo=ZoneInfo(KOLKATA)).astimezone(UTC),  # Fri 25 Sep
    datetime(2026, 9, 28, tzinfo=ZoneInfo(KOLKATA)).astimezone(UTC),  # to Mon 28 Sep
)


def test_the_weekend_before_an_anchor() -> None:
    w = window("the weekend before", anchor=GOA, anchor_label="Goa trip")
    assert span(w) == ("2026-09-19T00:00", "2026-09-21T00:00")
    assert w.anchor == "Goa trip"
    assert w.rule == "anchor_weekend_before"


def test_the_weekend_before_an_anchor_that_starts_on_a_saturday() -> None:
    saturday = (datetime(2026, 9, 26, tzinfo=ZoneInfo(KOLKATA)).astimezone(UTC), None)
    assert span(window("the weekend before", anchor=saturday)) == (
        "2026-09-19T00:00",
        "2026-09-21T00:00",
    )


def test_the_week_after_an_anchor_starts_the_day_after_it_ends() -> None:
    assert span(window("the week after", anchor=GOA)) == ("2026-09-28T00:00", "2026-10-05T00:00")


def test_before_and_since_an_anchor() -> None:
    assert span(window("before", anchor=GOA)) == (None, "2026-09-25T00:00")
    assert span(window("since", anchor=GOA)) == ("2026-09-25T00:00", "2026-10-06T10:00")


# ------------------------------------------------------------------ timezones


def test_this_week_across_a_dst_change_is_seven_local_days() -> None:
    new_york = at("2026-10-29T12:00", "America/New_York")  # DST ends Sun 1 Nov 2026
    w = window("this week", new_york)
    assert span(w, "America/New_York") == ("2026-10-26T00:00", "2026-11-02T00:00")
    assert w.start is not None
    assert w.end is not None
    assert (w.end - w.start).total_seconds() == 7 * 24 * 3600 + 3600  # one hour longer


def test_today_where_the_utc_date_differs_from_the_local_one() -> None:
    auckland = TurnNow(datetime(2026, 10, 6, 20, 0, tzinfo=UTC), "Pacific/Auckland")  # 7 Oct 09:00
    assert span(window("today", auckland), "Pacific/Auckland") == (
        "2026-10-07T00:00",
        "2026-10-08T00:00",
    )


# ------------------------------------------------------------------ what it can't read


def test_an_unknown_expression_is_refused_not_guessed() -> None:
    with pytest.raises(UnresolvableTimeError):
        window("when the moon is blue")


def test_contains_and_overlaps() -> None:
    w = window("last month")
    assert w.contains(datetime(2026, 9, 12, 6, 0, tzinfo=UTC))
    assert not w.contains(datetime(2026, 10, 1, 6, 0, tzinfo=UTC))
    assert w.overlaps(datetime(2026, 8, 30, tzinfo=UTC), datetime(2026, 9, 2, tzinfo=UTC))

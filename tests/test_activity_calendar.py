"""«Активность»'s month calendar (app/api/activity.py): whole Monday-to-Sunday weeks, a
day's level on the profile heatmap's scale, the streak chain, the month's figures and
which day is shown under it. Pure functions - no database."""

from datetime import date

from app.api.activity import (
    _add_months,
    _day_heading,
    _minutes_label,
    _month_calendar,
    _selected_day,
)
from app.db.activity import TitleDay

TODAY = date(2026, 10, 31)  # a Saturday
OCTOBER = date(2026, 10, 1)  # a Thursday
FIRST = _add_months(OCTOBER, -11)


def _calendar(days: dict[str, list[TitleDay]], month: date = OCTOBER):
    end = _add_months(month, 1).replace(day=1)
    end = date.fromordinal(end.toordinal() - 1)
    return _month_calendar(month, end, days, TODAY, FIRST)


def _read(chapters: int = 1, seconds: int = 0, slug: str = "1--a") -> list[TitleDay]:
    return [TitleDay(slug_url=slug, chapters=chapters, seconds=seconds)]


def _cell(calendar, day: int):
    return next(c for week in calendar.weeks for c in week if c.day and c.day.day == day)


def test_whole_weeks_with_blanks_around_the_month() -> None:
    calendar = _calendar({})

    assert calendar.title == "Октябрь 2026"
    assert all(len(week) == 7 for week in calendar.weeks)
    assert [c.day for c in calendar.weeks[0][:3]] == [None, None, None]  # Mon-Wed
    assert calendar.weeks[0][3].day == OCTOBER
    assert sum(1 for week in calendar.weeks for c in week if c.day) == 31
    assert calendar.prev_month == "2026-09"
    assert calendar.next_month is None
    assert calendar.elapsed_days == 31


def test_level_follows_reading_time_like_the_profile() -> None:
    calendar = _calendar(
        {
            "2026-10-05": _read(chapters=2),  # chapters, no time: 1
            "2026-10-06": _read(seconds=20 * 60),  # 15-45 min: 2
            "2026-10-07": _read(seconds=95 * 60),  # 90+ min: 4
        }
    )

    assert [_cell(calendar, d).level for d in (4, 5, 6, 7)] == [0, 1, 2, 4]
    assert _cell(calendar, 7).label == "7 октября: 1 глава, 1 ч 35 мин"
    assert _cell(calendar, 4).label == "4 октября"


def test_consecutive_days_are_chained_across_a_week_row() -> None:
    # Sun 11 - Mon 12 - Tue 13 run over the end of a week row.
    calendar = _calendar({f"2026-10-{d}": _read() for d in (11, 12, 13, 20)})

    sunday, monday, tuesday = (_cell(calendar, d) for d in (11, 12, 13))
    assert (sunday.chain_before, sunday.chain_after) == (False, True)
    assert (monday.chain_before, monday.chain_after) == (True, True)
    assert (tuesday.chain_before, tuesday.chain_after) == (True, False)
    lone = _cell(calendar, 20)
    assert (lone.chain_before, lone.chain_after) == (False, False)
    assert _cell(calendar, 14).chain_before is False  # not read itself


def test_month_figures_add_up_from_the_days() -> None:
    calendar = _calendar(
        {
            "2026-10-01": _read(chapters=3, seconds=600),
            "2026-10-02": _read(chapters=1, seconds=0) + _read(2, 1200, "2--b"),
            "2026-10-03": _read(chapters=0, seconds=300),
            "2026-10-10": _read(chapters=4, seconds=3600),
        }
    )

    assert calendar.reading_days == 4
    assert calendar.chapters == 10
    assert calendar.time_label == "1 ч 35 мин"
    assert calendar.best_streak == 3


def test_a_past_month_counts_all_its_days_and_links_forward() -> None:
    calendar = _calendar({}, month=date(2026, 9, 1))

    assert calendar.elapsed_days == 30
    assert calendar.next_month == "2026-10"
    assert all(not c.is_future for week in calendar.weeks for c in week)


def test_future_days_and_today() -> None:
    calendar = _calendar({}, month=OCTOBER)

    assert _cell(calendar, 31).is_today
    november = _month_calendar(date(2026, 11, 1), date(2026, 11, 30), {}, date(2026, 11, 15), FIRST)
    assert _cell(november, 16).is_future
    assert not _cell(november, 15).is_future


def test_the_first_month_has_no_way_back() -> None:
    assert _calendar({}, month=FIRST).prev_month is None


def test_selected_day() -> None:
    assert _selected_day(date(2026, 10, 3), [], OCTOBER, TODAY) == date(2026, 10, 3)
    assert _selected_day(None, [], OCTOBER, TODAY) == TODAY
    # A picked day of another month, or in the future, falls back.
    assert _selected_day(date(2026, 9, 3), [], OCTOBER, TODAY) == TODAY
    # A past month without a pick: its latest reading day, or nothing.
    assert _selected_day(None, [], date(2026, 9, 1), TODAY) is None


def test_day_heading_and_minutes() -> None:
    assert _day_heading(TODAY, TODAY) == "Сегодня"
    assert _day_heading(date(2026, 10, 30), TODAY) == "Вчера"
    assert _day_heading(date(2026, 10, 3), TODAY) == "3 октября"
    assert _day_heading(date(2025, 12, 3), TODAY) == "3 декабря 2025"
    assert _minutes_label(0) is None
    assert _minutes_label(30) == "< 1 мин"
    assert _minutes_label(125 * 60) == "2 ч 5 мин"


def test_add_months_crosses_years() -> None:
    assert _add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)
    assert _add_months(date(2026, 12, 1), 1) == date(2027, 1, 1)
    assert _add_months(OCTOBER, -11) == date(2025, 11, 1)

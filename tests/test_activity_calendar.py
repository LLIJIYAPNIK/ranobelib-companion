"""«Активность»'s month calendar (app/api/activity.py): whole Monday-to-Sunday weeks, a
day's level on the profile heatmap's scale, the month's reading by hour of the day, when a
day was read and which day is shown beside the calendar. Pure functions - no database."""

from datetime import date

from app.api.activity import (
    _add_months,
    _day_heading,
    _minutes_label,
    _month_calendar,
    _reading_hours,
    _selected_day,
    _span_label,
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


def test_reading_hours_follow_the_hours_and_name_the_busiest_part() -> None:
    hours = [0] * 24
    hours[7] = 600  # 10 min in the morning
    hours[21] = 1800  # 30 min
    hours[22] = 3000  # 50 min - the peak
    days = {"2026-10-01": _read(chapters=3, seconds=5400), "2026-10-02": _read(chapters=2)}

    reading = _reading_hours(days, hours)

    assert reading.time_label == "1 ч 30 мин"  # the sum of the hours shown
    assert reading.chapters == 5
    assert reading.part_label == "вечером"
    assert reading.part_share == 89  # 80 of 90 minutes
    assert reading.peak_label == "22:00–23:00"
    assert [bar.height_percent for bar in reading.bars if bar.minutes] == [20, 60, 100]
    assert [bar.hour for bar in reading.bars if bar.is_peak] == [22]
    assert reading.bars[22].tooltip == "22:00–23:00 · 50 мин"
    assert reading.bars[3].tooltip == "03:00–04:00 · не читали"


def test_reading_hours_of_a_month_without_reading_time() -> None:
    reading = _reading_hours({}, [0] * 24)

    assert reading.time_label == "0 мин"
    assert reading.part_label is None
    assert reading.peak_label is None
    assert all(bar.height_percent == 0 and not bar.is_peak for bar in reading.bars)


def test_day_span_is_on_the_readers_clock() -> None:
    entries = [
        TitleDay("1--a", 2, 600, "2026-10-01T18:05:00+00:00", "2026-10-01T19:40:00+00:00"),
        TitleDay("2--b", 1, 60, "2026-10-01T06:12:00+00:00", "2026-10-01T06:30:00+00:00"),
    ]

    assert _span_label(entries, "Europe/Moscow") == "09:12–22:40"
    one = [TitleDay("1--a", 1, 0, "2026-10-01T18:05:00+00:00", "2026-10-01T18:05:30+00:00")]
    assert _span_label(one, "UTC") == "в 18:05"
    assert _span_label([TitleDay("1--a", 1, 0)], "UTC") is None


def test_a_past_month_links_forward() -> None:
    calendar = _calendar({}, month=date(2026, 9, 1))

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

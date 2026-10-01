from datetime import date

from app.api.library import _last_read_label

TODAY = date(2026, 10, 1)


def test_no_last_read_means_no_label() -> None:
    assert _last_read_label(None, TODAY) is None


def test_today_and_yesterday() -> None:
    assert _last_read_label("2026-10-01T08:00:00+00:00", TODAY) == "Читали сегодня"
    assert _last_read_label("2026-09-30T23:59:00+00:00", TODAY) == "Читали вчера"


def test_older_date_in_genitive_case() -> None:
    assert _last_read_label("2026-09-06T12:00:00+00:00", TODAY) == "Последнее чтение 6 сентября"


def test_previous_year_adds_the_year() -> None:
    assert _last_read_label("2025-05-02T12:00:00+00:00", TODAY) == "Последнее чтение 2 мая 2025"


def test_offset_is_converted_to_utc_and_naive_is_utc() -> None:
    # 01:30 at +03:00 is still the previous UTC day.
    assert _last_read_label("2026-10-01T01:30:00+03:00", TODAY) == "Читали вчера"
    assert _last_read_label("2026-10-01T00:10:00", TODAY) == "Читали сегодня"

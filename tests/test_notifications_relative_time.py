"""relative_time() (PR 311) - the notification card's «5 мин назад»."""

from datetime import UTC, datetime, timedelta

import pytest

from app.api.notifications import relative_time

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("ago", "expected"),
    [
        (timedelta(seconds=0), "только что"),
        (timedelta(seconds=59), "только что"),
        (timedelta(minutes=1), "1 мин назад"),
        (timedelta(minutes=59, seconds=59), "59 мин назад"),
        (timedelta(hours=1), "1 ч назад"),
        (timedelta(hours=23, minutes=59), "23 ч назад"),
        (timedelta(days=1), "1 дн. назад"),
        (timedelta(days=6, hours=23), "6 дн. назад"),
        (timedelta(days=7), "30 сент."),
        (timedelta(days=279), "1 янв."),
        (timedelta(days=280), "31 дек. 2025"),
    ],
)
def test_relative_time(ago: timedelta, expected: str) -> None:
    assert relative_time(NOW - ago, NOW) == expected


def test_relative_time_treats_a_moment_in_the_future_as_just_now() -> None:
    assert relative_time(NOW + timedelta(minutes=3), NOW) == "только что"

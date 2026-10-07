"""PR 323: the profile reading calendar's colors match its legend and its own tooltips.

A day's level comes from that day's real reading time (heartbeat seconds) on fixed
thresholds shared by everyone - 0 / under 15 min / 15-45 / 45-90 / 90+ min - with at least
level 1 for a day with chapters opened but no reading time. Not from the chapter count
relative to the user's own busiest day, where one outlier repainted the rest of the year.
"""

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.connection import connection
from app.db.users import get_user_by_email
from tests.auth_helpers import register
from tests.db_reset import reset_app_database
from tests.test_api_profile import _fake_title, _FakeClient

CELL = re.compile(
    r'class="reading-calendar__day reading-calendar__day--level-(\d)([^"]*)"\s+'
    r'data-tooltip="(\d\d\.\d\d\.\d{4}):([^"\n]*)'
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


async def _seed(email: str, days: dict[date, tuple[int, int]]) -> None:
    """`days`: date -> (chapters opened, heartbeat seconds), at noon UTC."""
    async with connection() as conn:
        user = await get_user_by_email(conn, email)
        assert user is not None
        for day, (chapters, seconds) in days.items():
            at = f"{day.isoformat()}T12:00:00+00:00"
            for _ in range(chapters):
                await conn.execute(
                    "INSERT INTO activity_events (user_id, kind, slug_url, volume, number, "
                    "created_at) VALUES (%s, 'chapter_read', '6712--test-novel', '1', '1', %s)",
                    (user.id, at),
                )
            if seconds:
                await conn.execute(
                    "INSERT INTO activity_events (user_id, kind, slug_url, seconds, created_at) "
                    "VALUES (%s, 'heartbeat', '6712--test-novel', %s, %s)",
                    (user.id, seconds, at),
                )


def _cells(client: TestClient) -> list[tuple[date, int, str, str]]:
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        html = client.get("/profile").text
    return [
        (datetime.strptime(day, "%d.%m.%Y").date(), int(level), extra, label.strip())
        for level, extra, day, label in CELL.findall(html)
    ]


def _levels(client: TestClient) -> dict[date, int]:
    return {day: level for day, level, _, _ in _cells(client)}


TODAY = datetime.now(UTC).date()
ONE_CHAPTER_NO_TIME = TODAY - timedelta(days=1)
TIME_ONLY_40_MIN = TODAY - timedelta(days=2)
TEN_CHAPTERS_2_HOURS = TODAY - timedelta(days=3)
OUTLIER_60_CHAPTERS_20_MIN = TODAY - timedelta(days=4)
TEN_MINUTES = TODAY - timedelta(days=5)
SET = {
    ONE_CHAPTER_NO_TIME: (1, 0),
    TIME_ONLY_40_MIN: (0, 40 * 60),
    TEN_CHAPTERS_2_HOURS: (10, 2 * 3600),
    OUTLIER_60_CHAPTERS_20_MIN: (60, 20 * 60),
    TEN_MINUTES: (1, 10 * 60),
}


async def test_levels_follow_reading_time_on_fixed_thresholds(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass")
    await _seed("alice@example.com", SET)

    levels = _levels(client)

    assert levels[ONE_CHAPTER_NO_TIME] == 1  # chapters opened, no time logged: at least 1
    assert levels[TEN_MINUTES] == 1  # under 15 min
    assert levels[OUTLIER_60_CHAPTERS_20_MIN] == 2  # 15-45 min - the count doesn't matter
    assert levels[TIME_ONLY_40_MIN] == 2  # no chapter opened, still a reading day
    assert levels[TEN_CHAPTERS_2_HOURS] == 4  # 90+ min, whatever the outlier did
    assert levels[TODAY] == 0


async def test_one_outlier_does_not_repaint_the_other_days(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass")
    await _seed("alice@example.com", {TEN_CHAPTERS_2_HOURS: (10, 2 * 3600)})
    alone = _levels(client)[TEN_CHAPTERS_2_HOURS]

    await _seed("alice@example.com", {OUTLIER_60_CHAPTERS_20_MIN: (60, 20 * 60)})

    assert _levels(client)[TEN_CHAPTERS_2_HOURS] == alone == 4


@pytest.mark.parametrize(
    ("minutes", "level"),
    [(1, 1), (14, 1), (15, 2), (44, 2), (45, 3), (89, 3), (90, 4), (300, 4)],
)
async def test_threshold_edges(client: TestClient, minutes: int, level: int) -> None:
    register(client, "alice@example.com", "hunter2pass")
    await _seed("alice@example.com", {ONE_CHAPTER_NO_TIME: (0, minutes * 60)})

    assert _levels(client)[ONE_CHAPTER_NO_TIME] == level


async def test_every_cell_color_agrees_with_its_own_tooltip(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass")
    await _seed("alice@example.com", SET)

    for _day, level, _extra, label in _cells(client):
        reading = "нет прочитанных глав, 0 мин" not in label
        assert (level > 0) == reading, label


async def test_legend_spells_out_the_thresholds(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        html = client.get("/profile").text

    legend = html[html.index('class="reading-calendar-legend"') :]
    legend = legend[: legend.index("</div>")]
    for text in ("Нет чтения", "до 15 мин", "15–45 мин", "45–90 мин", "больше 90 мин"):
        assert f'title="{text}"' in legend
        assert f'aria-label="{text}"' in legend
    assert 'aria-hidden="true"' not in legend[: legend.index(">")]


async def test_reading_days_counts_time_only_days(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass")
    await _seed("alice@example.com", {TIME_ONLY_40_MIN: (0, 40 * 60), TEN_MINUTES: (1, 600)})
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        html = client.get("/profile").text

    assert "2 дня с чтением" in html


async def test_weeks_start_on_monday_like_the_weekday_labels(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        html = client.get("/profile").text
    cells = _cells(client)

    assert cells[0][0].weekday() == 0  # the grid's first row is Monday
    assert cells[-1][0] == TODAY
    weekdays = html[html.index('class="reading-calendar-weekdays"') :]
    labels = re.findall(r"<span>([^<]*)</span>", weekdays[: weekdays.index("</div>")])
    assert labels == ["Пн", "", "Ср", "", "Пт", "", ""]


async def test_today_is_marked(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass")

    marked = [day for day, _, extra, _ in _cells(client) if "reading-calendar__day--today" in extra]

    assert marked == [TODAY]

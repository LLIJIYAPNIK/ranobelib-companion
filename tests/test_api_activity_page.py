"""GET /activity - the "Активность" page (see app/api/activity.py, show_activity): the
month calendar of reading days, the picked day, friends' reading and what people read on
the site."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib.models import Chapter, Cover, Label, Title, Volume

import app.api.activity as activity_api
import app.jobs.store as job_store
from app.config import get_settings
from app.db.activity import record_chapter_read, record_heartbeat
from app.db.comments import create_comment
from app.db.connection import connection
from app.db.downloads import record_download
from app.db.friendships import accept_request, send_request
from app.db.library import add_entry, record_progress
from app.db.users import update_privacy_settings
from app.timezones import local_today
from tests.auth_helpers import register
from tests.db_reset import reset_app_database


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()
    monkeypatch.setattr(job_store, "_jobs", {})
    # ranobelib.me's most viewed (the fallback of «Популярное») - never the network.
    monkeypatch.setattr(activity_api, "popular_titles", _no_popular)

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


async def _no_popular(limit: int = 12) -> list[Title]:
    return []


def _register(client: TestClient, email: str = "alice@example.com") -> None:
    register(client, email)


def _days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


class _FakeClient:
    def __init__(self, title: Title, volumes: list[Volume] | None = None) -> None:
        self._title = title
        self._volumes = volumes or []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_info(self) -> Title:
        return self._title

    async def get_table_of_contents(self) -> list[Volume]:
        return self._volumes


def _fake_title(slug_url: str = "6712--test-novel") -> Title:
    return Title(
        id=6712,
        name="Test Novel",
        slug="test-novel",
        slug_url=slug_url,
        cover=Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
    )


def test_show_activity_anonymous_is_viewable_but_prompts_to_log_in(
    client: TestClient,
) -> None:
    response = client.get("/activity")

    assert response.status_code == 200
    assert 'data-role="locked-feature"' in response.text
    assert 'href="/register"' in response.text
    assert "activity-calendar.js" not in response.text


def test_show_activity_empty_state(client: TestClient) -> None:
    _register(client)

    response = client.get("/activity")

    assert response.status_code == 200
    assert "Откройте любую главу — дни чтения появятся в календаре" in response.text
    assert "В этот день не читали." in response.text
    assert "ac-day--read" not in response.text
    assert "Здесь появятся прочитанные главы и загрузки." in response.text
    # No friends yet: the block offers to find some.
    assert 'data-role="friends-activity"' in response.text
    assert "Найти друзей по нику" in response.text
    assert "static/js/activity-calendar.js" in response.text


def test_show_activity_calendar_is_the_current_month_in_whole_weeks(client: TestClient) -> None:
    _register(client)
    today = local_today()

    response = client.get("/activity")

    months = activity_api._MONTHS_NOMINATIVE
    assert f"{months[today.month - 1]} {today.year}" in response.text
    assert response.text.count('class="ac-cal__row" role="row"') in (4, 5, 6)
    assert f'href="/activity?day={today.isoformat()}"' in response.text
    assert 'aria-current="date"' in response.text
    # The current month has no «next»; the previous one is a link.
    assert 'aria-label="Следующий месяц"' not in response.text
    assert 'aria-label="Предыдущий месяц"' in response.text


async def test_show_activity_marks_reading_days_and_the_streak(client: TestClient) -> None:
    _register(client)  # user id 1
    async with connection() as conn:
        await record_chapter_read(conn, 1, "6712--test-novel", "1", "5")
        await record_chapter_read(conn, 1, "6712--test-novel", "1", "4")
        await conn.execute(
            "UPDATE activity_events SET created_at = %s WHERE number = '4'", (_days_ago(1),)
        )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        response = client.get("/activity")

    assert '<b class="hm-num">2 дня</b> подряд' in response.text
    assert "Сегодня 1 глава" in response.text
    if local_today().day > 1:  # yesterday is in this month too
        assert response.text.count("ac-day--read") == 2


async def test_show_activity_lists_what_was_read_today(client: TestClient) -> None:
    _register(client)  # user id 1
    async with connection() as conn:
        await record_chapter_read(conn, 1, "6712--test-novel", "2", "8")
        await record_chapter_read(conn, 1, "6712--test-novel", "2", "9")
        await record_heartbeat(conn, 1, "6712--test-novel", 50)

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        response = client.get("/activity")

    today = local_today().isoformat()
    panel = response.text[response.text.index(f'data-day-panel="{today}"') :]
    panel = panel[: panel.index("</section>")]
    assert "Test Novel" in panel
    assert "2 главы · &lt; 1 мин" in panel
    assert 'href="/titles/6712--test-novel/chapters/2/9"' in panel  # «Продолжить чтение»


async def test_show_activity_prefers_russian_name(client: TestClient) -> None:
    _register(client)  # user id 1
    async with connection() as conn:
        await record_chapter_read(conn, 1, "6712--test-novel", "1", "5")
    title = Title(
        id=6712,
        name="Test Novel",
        rus_name="Тестовый роман",
        slug="test-novel",
        slug_url="6712--test-novel",
        cover=Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
    )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/activity")

    assert response.status_code == 200
    assert "Тестовый роман" in response.text
    assert "Test Novel" not in response.text


async def test_show_activity_shows_when_the_month_was_read(client: TestClient) -> None:
    """Reading time without a chapter opened still marks the day (PR 323) and counts in
    «Когда вы читаете»."""
    _register(client)  # user id 1
    async with connection() as conn:
        await record_heartbeat(conn, 1, "6712--test-novel", 90 * 60)

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        response = client.get("/activity")

    hours = response.text[response.text.index('data-role="reading-hours"') :]
    assert "<b>1 ч 30 мин</b> и 0 глав за " in hours
    assert '<b class="hm-num">100%</b> времени' in hours
    assert hours.count("ac-hours__bar--peak") == 1
    assert hours.count('class="ac-hours__bar') == 24
    assert "ac-day--l4" in response.text  # 90+ minutes
    assert "Лучшая серия" not in response.text
    assert "Дней с чтением" not in response.text


async def test_show_activity_recent_events_show_the_cover(client: TestClient) -> None:
    _register(client)  # user id 1
    title = _fake_title().model_copy(update={"cover": Cover(default="https://cover.example/a.jpg")})
    async with connection() as conn:
        await record_chapter_read(conn, 1, "6712--test-novel", "1", "5")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/activity")

    feed = response.text[response.text.index('data-role="activity-events"') :]
    assert '<img src="https://cover.example/a.jpg"' in feed


async def test_show_activity_day_parameter_picks_a_past_day(client: TestClient) -> None:
    _register(client)  # user id 1
    async with connection() as conn:
        await record_chapter_read(conn, 1, "6712--test-novel", "1", "5")
        await conn.execute("UPDATE activity_events SET created_at = %s", (_days_ago(40),))
    day = (local_today() - timedelta(days=40)).isoformat()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        response = client.get(f"/activity?day={day}")

    assert response.status_code == 200
    shown = response.text.index(f'data-day-panel="{day}"')
    assert "hidden" not in response.text[shown : response.text.index(">", shown)]
    assert 'aria-label="Следующий месяц"' in response.text


def test_show_activity_rejects_bad_dates(client: TestClient) -> None:
    _register(client)

    assert client.get("/activity?month=2026-13").status_code == 422
    assert client.get("/activity?day=2026-02-31").status_code == 422
    # Out of the calendar's window: the current month, not an error.
    assert client.get("/activity?month=1999-01").status_code == 200


async def test_show_activity_event_feed_merges_reads_and_downloads(client: TestClient) -> None:
    _register(client)  # user id 1
    volumes = [Volume(number="1", chapters=[Chapter(id=5, volume="1", number="5", name="Начало")])]
    async with connection() as conn:
        await record_download(conn, 1, "6712--test-novel", "epub", "error", None, "boom")
        await record_chapter_read(conn, 1, "6712--test-novel", "1", "5")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title(), volumes)):
        response = client.get("/activity")

    feed = response.text[response.text.index('data-role="activity-events"') :]
    read_at = feed.index("Прочитана глава 5 · Test Novel")
    failed_at = feed.index("Не удалось скачать EPUB · Test Novel")
    assert read_at < failed_at  # newest first
    assert "Том 1 · Начало" in feed
    assert 'href="/titles/6712--test-novel/chapters/1/5"' in feed
    assert "boom" not in feed  # the feed says "Ошибка загрузки", not the raw error
    assert ">Все загрузки</a>" in response.text


# --- Друзья ---------------------------------------------------------------------------


async def _befriend(client: TestClient) -> None:
    """Bob (id 1) and Alice (id 2) are friends; the session is Alice's."""
    _register(client, "bob@example.com")
    _register(client, "alice@example.com")
    async with connection() as conn:
        await send_request(conn, 1, 2)
        await accept_request(conn, 2, 1)


async def test_show_activity_shows_what_friends_read(client: TestClient) -> None:
    await _befriend(client)
    async with connection() as conn:
        await add_entry(conn, 1, "6712--test-novel")
        await record_progress(conn, 1, "6712--test-novel", volume="3", number="54")
        await record_chapter_read(conn, 1, "6712--test-novel", "3", "54")
        await create_comment(conn, 1, "6712--test-novel", "3", "54", "", 0, "Отличная глава!")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        response = client.get("/activity")

    block = response.text[response.text.index('data-role="friends-activity"') :]
    assert "Test Novel" in block
    assert "Том 3 · глава 54" in block
    assert "1 день подряд" in block
    assert "«Отличная глава!»" in block
    assert "сегодня в " in block


async def test_show_activity_hides_a_friends_book_they_keep_private(client: TestClient) -> None:
    await _befriend(client)
    async with connection() as conn:
        await add_entry(conn, 1, "6712--test-novel")
        await record_progress(conn, 1, "6712--test-novel", volume="3", number="54")
        await update_privacy_settings(
            conn,
            1,
            show_currently_reading=False,
            show_library=True,
            show_friends_activity_home=True,
            show_friends=True,
        )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        response = client.get("/activity")

    block = response.text[response.text.index('data-role="friends-activity"') :]
    assert "Том 3 · глава 54" not in block


async def test_show_activity_omits_friends_when_the_reader_turned_them_off(
    client: TestClient,
) -> None:
    await _befriend(client)
    async with connection() as conn:
        await update_privacy_settings(
            conn,
            2,
            show_currently_reading=True,
            show_library=True,
            show_friends_activity_home=False,
            show_friends=True,
        )

    response = client.get("/activity")

    assert 'data-role="friends-activity"' not in response.text


async def test_show_activity_folds_friends_past_the_first_five(client: TestClient) -> None:
    _register(client, "me@example.com")  # id 1, nobody's friend
    for n in range(2, 10):  # eight friends, ids 2-9
        _register(client, f"friend{n}@example.com")
    _register(client, "me@example.com".replace("me", "me2"))  # id 10, the viewer
    async with connection() as conn:
        for friend_id in range(2, 10):
            await send_request(conn, friend_id, 10)
            await accept_request(conn, 10, friend_id)
        await record_chapter_read(conn, 9, "6712--test-novel", "1", "1")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        response = client.get("/activity")

    block = response.text[response.text.index('data-role="friends-activity"') :]
    assert '<span class="ac-section-head__count hm-num">8</span>' in block
    assert block.count('class="ac-friend"') == 5
    assert "Ещё 3 друга" in block
    assert block.count('class="ac-more__item"') == 3
    # The friend who read is first.
    first = block.index('class="ac-friend"')
    assert block.index('href="/profile/9"', first) < block.index('href="/profile/2"', first)


# --- Популярное на Webnovells --------------------------------------------------------------------


async def test_show_activity_lists_titles_read_on_the_site(client: TestClient) -> None:
    _register(client, "bob@example.com")
    _register(client, "alice@example.com")
    async with connection() as conn:
        for user_id, slug in [(1, "1--a"), (2, "1--a"), (1, "2--b"), (2, "3--c"), (2, "3--c")]:
            await record_chapter_read(conn, user_id, slug, "1", "1")
        await record_chapter_read(conn, 2, "4--old", "1", "1")
        await conn.execute(
            "UPDATE activity_events SET created_at = %s WHERE slug_url = '4--old'",
            (_days_ago(10),),
        )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        response = client.get("/activity")

    block = response.text[response.text.index('data-role="popular-titles"') :]
    assert "Популярное на Webnovells" in block
    assert "2 читателя · 2 главы" in block  # 1--a
    assert block.index('href="/titles/1--a"') < block.index('href="/titles/3--c"')
    assert block.index('href="/titles/3--c"') < block.index('href="/titles/2--b"')
    assert "4--old" not in block  # read 10 days ago
    assert 'style="width: 100%"' in block
    assert 'style="width: 50%"' in block


async def test_show_activity_falls_back_to_ranobelib_popular(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(client)

    async def viewed(limit: int = 12) -> list[Title]:
        return [_fake_title("9--viewed")]

    monkeypatch.setattr(activity_api, "popular_titles", viewed)
    response = client.get("/activity")

    block = response.text[response.text.index('data-role="popular-titles"') :]
    assert "По просмотрам на ranobelib.me" in block
    assert 'href="/titles/9--viewed"' in block
    assert "читател" not in block

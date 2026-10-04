"""GET /activity - the "Активность" page (see app/api/activity.py, show_activity)."""

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib.models import Chapter, Cover, Label, Title, Volume

import app.jobs.store as job_store
from app.config import get_settings
from app.db.activity import record_chapter_read, record_heartbeat
from app.db.connection import connection
from app.db.downloads import record_download
from app.jobs.store import create_job
from tests.auth_helpers import register
from tests.db_reset import reset_app_database


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()
    monkeypatch.setattr(job_store, "_jobs", {})

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


def _register(client: TestClient, email: str = "alice@example.com") -> None:
    register(client, email)


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
    assert 'href="/login"' in response.text
    assert 'href="/register"' in response.text


def test_show_activity_empty_state(client: TestClient) -> None:
    _register(client)

    response = client.get("/activity")

    assert response.status_code == 200
    assert "Сегодня ещё ничего не читали" in response.text
    assert "Сейчас ничего не скачивается" in response.text
    assert "Сегодня ничего не скачивали" in response.text


async def test_show_activity_shows_chapters_read_today(client: TestClient) -> None:
    _register(client)  # user id 1
    async with connection() as conn:
        await record_chapter_read(conn, 1, "6712--test-novel", "1", "5")
        await record_chapter_read(conn, 1, "6712--test-novel", "1", "6")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        response = client.get("/activity")

    assert response.status_code == 200
    assert "Test Novel" in response.text
    assert "2 главы сегодня" in response.text


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


async def test_show_activity_shows_active_time(client: TestClient) -> None:
    _register(client)  # user id 1
    async with connection() as conn:
        await record_heartbeat(conn, 1, "6712--test-novel", 90 * 60)

    response = client.get("/activity")

    assert "1 ч 30 мин" in response.text


def test_show_activity_shows_active_job(client: TestClient) -> None:
    _register(client)  # user id 1
    job = create_job("6712--test-novel", "epub", user_id=1)
    job.status = "running"
    job.completed = 3
    job.total = 10

    response = client.get("/activity")

    assert response.status_code == 200
    assert f'data-job-id="{job.id}"' in response.text
    assert "Скачивание · 3 из 10 глав" in response.text
    assert "static/js/downloads-status.js" in response.text


async def test_show_activity_shows_downloads_today(client: TestClient) -> None:
    _register(client)  # user id 1
    async with connection() as conn:
        await record_download(conn, 1, "6712--test-novel", "epub", "done", 42, None)

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        response = client.get("/activity")

    assert response.status_code == 200
    assert "6712--test-novel" in response.text
    assert "42 глав" in response.text


# PR 276 (Webnovells Redesign, screen A4).


def test_show_activity_period_switch_marks_the_current_period(client: TestClient) -> None:
    _register(client)

    response = client.get("/activity?period=7d")

    assert response.status_code == 200
    assert 'href="/activity?period=7d" aria-current="page">7 дней</a>' in response.text
    assert 'href="/activity">Сегодня</a>' in response.text


def test_show_activity_rejects_an_unknown_period(client: TestClient) -> None:
    _register(client)

    assert client.get("/activity?period=year").status_code == 422


async def test_show_activity_metrics_follow_the_period(client: TestClient) -> None:
    """A read 3 days ago counts for "7 дней" but not for "Сегодня"."""
    _register(client)  # user id 1
    async with connection() as conn:
        await record_chapter_read(conn, 1, "6712--test-novel", "1", "5")
        await conn.execute(
            "UPDATE activity_events SET created_at = %s",
            ((datetime.now(UTC) - timedelta(days=3)).isoformat(),),
        )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        today = client.get("/activity")
        week = client.get("/activity?period=7d")

    chapters_today = re.search(
        r"Глав прочитано</span>\s*<span[^>]*>(\d+)", today.text
    ).group(1)
    chapters_week = re.search(r"Глав прочитано</span>\s*<span[^>]*>(\d+)", week.text).group(1)
    assert chapters_today == "0"
    assert chapters_week == "1"
    assert "1 день с чтением" in week.text  # "За последний год"


async def test_show_activity_chart_covers_30_days(client: TestClient) -> None:
    _register(client)  # user id 1
    async with connection() as conn:
        await record_heartbeat(conn, 1, "6712--test-novel", 6 * 60)

    response = client.get("/activity")

    assert response.text.count("wn-activity-chart__bar") - response.text.count(
        "wn-activity-chart__bar--"
    ) == 30
    assert "wn-activity-chart__bar--filled wn-activity-chart__bar--today" in response.text
    assert "6 мин · 1 день с чтением" in response.text


async def test_show_activity_event_feed_merges_reads_and_downloads(client: TestClient) -> None:
    _register(client)  # user id 1
    volumes = [
        Volume(number="1", chapters=[Chapter(id=5, volume="1", number="5", name="Начало")])
    ]
    async with connection() as conn:
        await record_download(conn, 1, "6712--test-novel", "epub", "error", None, "boom")
        await record_chapter_read(conn, 1, "6712--test-novel", "1", "5")

    with patch(
        "app.services.client.RanobeLib", return_value=_FakeClient(_fake_title(), volumes)
    ):
        response = client.get("/activity")

    feed = response.text[response.text.index('data-role="activity-events"') :]
    read_at = feed.index("Прочитана глава 5 · Test Novel")
    failed_at = feed.index("Не удалось скачать EPUB · Test Novel")
    assert read_at < failed_at  # newest first
    assert "Том 1 · Начало" in feed
    assert 'href="/titles/6712--test-novel/chapters/1/5"' in feed
    assert "boom" not in feed  # the feed says "Ошибка загрузки", not the raw error


async def test_show_activity_read_today_card_links_to_the_last_chapter(
    client: TestClient,
) -> None:
    _register(client)  # user id 1
    async with connection() as conn:
        await record_chapter_read(conn, 1, "6712--test-novel", "2", "9")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        response = client.get("/activity")

    assert "Том 2, глава 9" in response.text
    assert "1 глава сегодня" in response.text
    assert (
        '<a class="wn-activity-link" href="/titles/6712--test-novel/chapters/2/9">'
        "Продолжить</a>"
    ) in response.text
    assert "Продолжить →" not in response.text
    assert '>Все загрузки</a>' in response.text


async def test_show_activity_mentions_the_last_download_date(client: TestClient) -> None:
    _register(client)  # user id 1
    async with connection() as conn:
        await record_download(conn, 1, "6712--test-novel", "epub", "done", 3, None)
        await conn.execute(
            "UPDATE download_history SET finished_at = '2026-09-06T19:17:00+00:00'"
        )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        response = client.get("/activity")

    assert "Сегодня ничего не скачивали. Последняя загрузка — 6 сентября." in response.text
    assert "Последняя загрузка 06.09" in response.text


def test_show_activity_without_any_reading_shows_zero_minutes(client: TestClient) -> None:
    _register(client)

    response = client.get("/activity")

    assert "&lt;1" not in response.text
    assert "0 мин · 0 дней с чтением" in response.text

"""GET /activity - the "Активность" page (see app/api/activity.py, show_activity)."""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib.models import Cover, Label, Title

import app.db.connection as db_connection
import app.jobs.store as job_store
from app.config import get_settings
from app.db.activity import record_chapter_read, record_heartbeat
from app.db.connection import get_connection
from app.db.downloads import record_download
from app.jobs.store import create_job


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    get_settings.cache_clear()
    db_connection._connection = None
    monkeypatch.setattr(job_store, "_jobs", {})

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()
    db_connection._connection = None


def _register(client: TestClient, email: str = "alice@example.com") -> None:
    client.post(
        "/register",
        data={"email": email, "password": "hunter2pass", "password_confirm": "hunter2pass"},
    )


class _FakeClient:
    def __init__(self, title: Title) -> None:
        self._title = title

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_info(self) -> Title:
        return self._title


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


def test_show_activity_shows_chapters_read_today(client: TestClient) -> None:
    _register(client)  # user id 1
    record_chapter_read(get_connection(), 1, "6712--test-novel", "1", "5")
    record_chapter_read(get_connection(), 1, "6712--test-novel", "1", "6")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        response = client.get("/activity")

    assert response.status_code == 200
    assert "Test Novel" in response.text
    assert "2 глав сегодня" in response.text


def test_show_activity_shows_active_time(client: TestClient) -> None:
    _register(client)  # user id 1
    record_heartbeat(get_connection(), 1, "6712--test-novel", 90 * 60)

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
    assert "Глава 3 из 10" in response.text
    assert "static/js/downloads-status.js" in response.text


def test_show_activity_shows_downloads_today(client: TestClient) -> None:
    _register(client)  # user id 1
    record_download(get_connection(), 1, "6712--test-novel", "epub", "done", 42, None)

    response = client.get("/activity")

    assert response.status_code == 200
    assert "6712--test-novel" in response.text
    assert "42 глав" in response.text

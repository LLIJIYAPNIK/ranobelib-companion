"""build_activity_summary() (app/api/activity.py) - the aggregation behind the upcoming
"Активность" page. Exercised directly rather than over HTTP: no GET /activity route yet,
that lands with the UI in the next commit."""

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib import TitleNotFoundError
from ranobelib.models import Cover, Label, Title

import app.jobs.store as job_store
from app.api.activity import build_activity_summary
from app.config import get_settings
from app.db.activity import record_chapter_read, record_heartbeat
from app.db.connection import get_connection
from app.db.downloads import record_download
from app.db.users import User, get_user_by_email
from app.jobs.store import create_job
from tests.db_reset import reset_app_database


@pytest.fixture
def user(monkeypatch: pytest.MonkeyPatch) -> Iterator[User]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()
    monkeypatch.setattr(job_store, "_jobs", {})

    from app.main import app

    with TestClient(app) as client:
        client.post(
            "/register",
            data={
                "email": "alice@example.com",
                "password": "hunter2pass",
                "password_confirm": "hunter2pass",
            },
        )
        yield get_user_by_email(get_connection(), "alice@example.com")

    get_settings.cache_clear()


def _fake_title(name: str = "Test Novel", cover: Cover | None = None) -> Title:
    return Title(
        id=6712,
        name=name,
        slug="test-novel",
        slug_url="6712--test-novel",
        cover=cover or Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
    )


class _FakeClient:
    def __init__(self, title: Title | None = None, exc: Exception | None = None) -> None:
        self._title = title
        self._exc = exc

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_info(self) -> Title:
        if self._exc is not None:
            raise self._exc
        assert self._title is not None
        return self._title


async def test_build_activity_summary_empty_for_new_user(user: User) -> None:
    summary = await build_activity_summary(user)

    assert summary.read_today == []
    assert summary.chapters_read_today == 0
    assert summary.active_time_label == "< 1 мин"
    assert summary.active_jobs == []
    assert summary.downloads_today == []


async def test_build_activity_summary_counts_and_enriches_chapters_read_today(
    user: User,
) -> None:
    record_chapter_read(get_connection(), user.id, "6712--test-novel", "1", "5")
    record_chapter_read(get_connection(), user.id, "6712--test-novel", "1", "6")

    with patch(
        "app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())
    ):
        summary = await build_activity_summary(user)

    assert summary.chapters_read_today == 2
    assert len(summary.read_today) == 1
    item = summary.read_today[0]
    assert item.slug_url == "6712--test-novel"
    assert item.chapters_read == 2
    assert item.name == "Test Novel"


async def test_build_activity_summary_falls_back_when_title_unreachable(user: User) -> None:
    record_chapter_read(get_connection(), user.id, "6712--gone-novel", "1", "5")
    exc = TitleNotFoundError("6712--gone-novel")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(exc=exc)):
        summary = await build_activity_summary(user)

    item = summary.read_today[0]
    assert item.slug_url == "6712--gone-novel"
    assert item.name is None
    assert item.cover_url is None


async def test_build_activity_summary_formats_minutes_only(user: User) -> None:
    record_heartbeat(get_connection(), user.id, "6712--test-novel", 45 * 60)

    summary = await build_activity_summary(user)

    assert summary.active_time_label == "45 мин"


async def test_build_activity_summary_formats_hours_and_minutes(user: User) -> None:
    record_heartbeat(get_connection(), user.id, "6712--test-novel", 90 * 60)

    summary = await build_activity_summary(user)

    assert summary.active_time_label == "1 ч 30 мин"


async def test_build_activity_summary_includes_active_jobs(user: User) -> None:
    create_job("6712--test-novel", "epub", user_id=user.id)

    summary = await build_activity_summary(user)

    assert len(summary.active_jobs) == 1


async def test_build_activity_summary_omits_other_users_active_jobs(user: User) -> None:
    create_job("6712--other-novel", "epub", user_id=999)

    summary = await build_activity_summary(user)

    assert summary.active_jobs == []


async def test_build_activity_summary_includes_downloads_today(user: User) -> None:
    record_download(get_connection(), user.id, "6712--test-novel", "epub", "done", 10, None)

    summary = await build_activity_summary(user)

    assert len(summary.downloads_today) == 1
    assert summary.downloads_today[0].slug_url == "6712--test-novel"

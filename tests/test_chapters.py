from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib import (
    AuthRequiredError,
    ChapterNotFoundError,
    MultipleTranslationsError,
    RateLimitError,
)
from ranobelib.models import Chapter, ChapterBranch, ChapterUser, Team, Volume

import app.db.connection as db_connection
from app.config import get_settings
from app.db.activity import list_chapters_read_today
from app.db.connection import get_connection
from app.db.library import add_entry, get_entry
from app.main import app

client = TestClient(app)


class _FakeClient:
    def __init__(
        self,
        chapter: Chapter | None = None,
        exc: Exception | None = None,
        volumes: list[Volume] | None = None,
    ) -> None:
        self._chapter = chapter
        self._exc = exc
        self._volumes = volumes or []
        self.received_branch_id: int | None | str = "not called"

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_chapter(
        self, volume: int, number: str, *, branch_id: int | None = None
    ) -> Chapter:
        self.received_branch_id = branch_id
        if self._exc is not None:
            raise self._exc
        assert self._chapter is not None
        return self._chapter

    async def get_table_of_contents(self) -> list[Volume]:
        return self._volumes


def test_read_chapter_renders_heading_and_content() -> None:
    chapter = Chapter(
        id=1, volume="1", number="5", name="Начало", content="<p>Текст главы</p>"
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 200
    assert "Глава 5" in response.text
    assert "Начало" in response.text
    assert 'href="/titles/6712--test-novel"' in response.text
    assert "<p>Текст главы</p>" in response.text


def test_read_chapter_shows_adjacent_chapter_links() -> None:
    chapter = Chapter(id=2, volume="1", number="2", name="Середина", content="<p>x</p>")
    volumes = [
        Volume(
            number="1",
            chapters=[
                Chapter(id=1, volume="1", number="1"),
                Chapter(id=2, volume="1", number="2"),
                Chapter(id=3, volume="1", number="3"),
            ],
        )
    ]
    with patch(
        "app.services.client.RanobeLib",
        return_value=_FakeClient(chapter, volumes=volumes),
    ):
        response = client.get("/titles/6712--test-novel/chapters/1/2")

    assert response.status_code == 200
    assert 'href="/titles/6712--test-novel/chapters/1/1"' in response.text
    assert 'href="/titles/6712--test-novel/chapters/1/3"' in response.text


def test_read_chapter_crosses_volume_boundary() -> None:
    chapter = Chapter(id=1, volume="1", number="3", name="Конец тома", content="<p>x</p>")
    volumes = [
        Volume(number="1", chapters=[Chapter(id=1, volume="1", number="3")]),
        Volume(number="2", chapters=[Chapter(id=2, volume="2", number="1")]),
    ]
    with patch(
        "app.services.client.RanobeLib",
        return_value=_FakeClient(chapter, volumes=volumes),
    ):
        response = client.get("/titles/6712--test-novel/chapters/1/3")

    assert response.status_code == 200
    assert "Предыдущая глава" not in response.text
    assert 'href="/titles/6712--test-novel/chapters/2/1"' in response.text


def test_read_chapter_not_found() -> None:
    exc = ChapterNotFoundError("6712--test-novel", volume="1", number="999")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(exc=exc)):
        response = client.get(
            "/titles/6712--test-novel/chapters/1/999", headers={"accept": "text/html"}
        )

    assert response.status_code == 404
    assert "Глава не найдена" in response.text


def test_read_chapter_auth_required() -> None:
    exc = AuthRequiredError("https://ranobelib.me/x")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(exc=exc)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 403
    assert response.json() == {"detail": "Требуется авторизация — недоступно"}


def test_read_chapter_rate_limited() -> None:
    exc = RateLimitError(retry_after=30)
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(exc=exc)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 429
    assert response.json() == {
        "detail": "ranobelib сейчас ограничивает запросы, попробуйте позже"
    }


def test_read_chapter_malformed_slug_url_returns_friendly_404_not_500() -> None:
    # No RanobeLib patch here on purpose - see the equivalent test in test_titles.py.
    response = client.get("/titles/not-a-valid-slug/chapters/1/5")

    assert response.status_code == 404
    assert response.json() == {"detail": "Тайтл не найден, проверьте ссылку"}


def test_read_chapter_multiple_translations_shows_choice_page() -> None:
    branches = [
        ChapterBranch(
            id=1,
            branch_id=1,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            teams=[Team(id=1, slug="team-a", slug_url="team-a", name="Команда А")],
            user=ChapterUser(id=1, username="uploader1"),
        ),
        ChapterBranch(
            id=2,
            branch_id=2,
            created_at=datetime(2024, 3, 1, tzinfo=UTC),
            teams=[],
            user=ChapterUser(id=2, username="solo_translator"),
        ),
    ]
    exc = MultipleTranslationsError(
        "6712--test-novel", volume="1", number="5", branches=branches
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(exc=exc)):
        response = client.get(
            "/titles/6712--test-novel/chapters/1/5", headers={"accept": "text/html"}
        )

    assert response.status_code == 409
    assert "Команда А" in response.text
    assert "solo_translator" in response.text
    assert 'href="/titles/6712--test-novel/chapters/1/5?branch_id=1"' in response.text
    assert 'href="/titles/6712--test-novel/chapters/1/5?branch_id=2"' in response.text


def test_read_chapter_passes_branch_id_from_query_to_sdk() -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    fake = _FakeClient(chapter)
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get("/titles/6712--test-novel/chapters/1/5?branch_id=42")

    assert response.status_code == 200
    assert fake.received_branch_id == 42


def test_read_chapter_passes_no_branch_id_by_default() -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    fake = _FakeClient(chapter)
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 200
    assert fake.received_branch_id is None


@pytest.fixture
def logged_in_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Isolated DB + an authenticated session - see tests/test_api_auth.py for why the
    isolation dance (own DB file, reset connection singleton, `with TestClient`) is
    needed."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    get_settings.cache_clear()
    db_connection._connection = None

    with TestClient(app) as test_client:
        test_client.post(
            "/register",
            data={
                "email": "alice@example.com",
                "password": "hunter2pass",
                "password_confirm": "hunter2pass",
            },
        )
        yield test_client

    get_settings.cache_clear()
    db_connection._connection = None


def test_read_chapter_records_progress_for_title_in_library(
    logged_in_client: TestClient,
) -> None:
    add_entry(get_connection(), user_id=1, slug_url="6712--test-novel")
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        logged_in_client.get("/titles/6712--test-novel/chapters/1/5")

    entry = get_entry(get_connection(), user_id=1, slug_url="6712--test-novel")
    assert entry.last_read_volume == "1"
    assert entry.last_read_number == "5"


def test_read_chapter_does_not_add_title_to_library(logged_in_client: TestClient) -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = logged_in_client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 200
    assert get_entry(get_connection(), user_id=1, slug_url="6712--test-novel") is None


def test_read_chapter_includes_heartbeat_script_when_logged_in(
    logged_in_client: TestClient,
) -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = logged_in_client.get("/titles/6712--test-novel/chapters/1/5")

    assert "static/js/activity-heartbeat.js" in response.text
    assert 'data-slug-url="6712--test-novel"' in response.text


def test_read_chapter_omits_heartbeat_script_when_anonymous() -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert "static/js/activity-heartbeat.js" not in response.text


def test_read_chapter_records_activity_even_outside_the_library(
    logged_in_client: TestClient,
) -> None:
    # Deliberately not added to the library first - unlike record_progress, the activity
    # feed isn't gated on library membership (see app/db/activity.py).
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        logged_in_client.get("/titles/6712--test-novel/chapters/1/5")

    counts = list_chapters_read_today(get_connection(), user_id=1)
    assert [c.slug_url for c in counts] == ["6712--test-novel"]
    assert counts[0].chapters_read == 1


def test_read_chapter_anonymous_does_not_touch_the_database(tmp_path: Path) -> None:
    # No login here - just confirms current_user=None takes the no-op path rather than
    # erroring out trying to record progress for nobody.
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 200

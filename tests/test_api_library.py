"""End-to-end add/remove-from-library through the real ASGI app.

Same isolation strategy as tests/test_api_auth.py: a fresh on-disk SQLite file per test
and an explicit `with TestClient(app) as client:` so app.main's lifespan (migrations)
actually runs.
"""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib import TitleNotFoundError
from ranobelib.models import Chapter, Cover, Label, Title, Volume

import app.db.connection as db_connection
from app.config import get_settings
from app.db.connection import get_connection
from app.db.library import record_progress


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    get_settings.cache_clear()
    db_connection._connection = None

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


def _fake_title(slug_url: str = "6712--test-novel", cover: Cover | None = None) -> Title:
    return Title(
        id=6712,
        name="Test Novel",
        slug="test-novel",
        slug_url=slug_url,
        cover=cover or Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
    )


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

    async def estimate_title_size(self) -> int:
        return 0


class _FakeChapterClient:
    def __init__(self, chapter: Chapter) -> None:
        self._chapter = chapter

    async def __aenter__(self) -> "_FakeChapterClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_chapter(
        self, volume: int, number: str, *, branch_id: int | None = None
    ) -> Chapter:
        return self._chapter

    async def get_table_of_contents(self) -> list[Volume]:
        return []


def test_add_requires_login(client: TestClient) -> None:
    response = client.post(
        "/library/6712--test-novel/add", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_remove_requires_login(client: TestClient) -> None:
    response = client.post(
        "/library/6712--test-novel/remove", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_add_then_remove_round_trip(client: TestClient) -> None:
    _register(client)
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        add_response = client.post(
            "/library/6712--test-novel/add", follow_redirects=False
        )
        title_page = client.get("/titles/6712--test-novel")

    assert add_response.status_code == 303
    assert add_response.headers["location"] == "/titles/6712--test-novel"
    assert "Убрать из библиотеки" in title_page.text

    remove_response = client.post(
        "/library/6712--test-novel/remove", follow_redirects=False
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        title_page_after = client.get("/titles/6712--test-novel")

    assert remove_response.status_code == 303
    assert remove_response.headers["location"] == "/library"
    assert "Добавить в библиотеку" in title_page_after.text


def test_reading_a_chapter_auto_adds_title_and_the_page_reflects_it(
    client: TestClient,
) -> None:
    """PR 35: no explicit "Добавить" click here, just opening a chapter - the title
    page should already show the "in library" state on the very next load."""
    _register(client)
    title = _fake_title()
    chapter = Chapter(id=1, volume="1", number="1", content="<p>x</p>")

    with patch("app.services.client.RanobeLib", return_value=_FakeChapterClient(chapter)):
        chapter_response = client.get("/titles/6712--test-novel/chapters/1/1")
    assert chapter_response.status_code == 200

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        title_page = client.get("/titles/6712--test-novel")

    assert "Убрать из библиотеки" in title_page.text


def test_title_page_shows_reading_progress_for_library_entry(client: TestClient) -> None:
    _register(client)
    title = _fake_title()
    volumes = [
        Volume(
            number="1",
            chapters=[Chapter(id=i, volume="1", number=str(i)) for i in range(1, 5)],
        )
    ]

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        client.post("/library/6712--test-novel/add")

    record_progress(
        get_connection(), user_id=1, slug_url="6712--test-novel", volume="1", number="3"
    )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        response = client.get("/titles/6712--test-novel")

    assert response.status_code == 200
    assert "Прочитано 75%" in response.text  # 3 of 4 chapters
    assert 'style="width: 75%"' in response.text


def test_title_page_omits_reading_progress_when_not_in_library(client: TestClient) -> None:
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/6712--test-novel")

    assert response.status_code == 200
    assert "Прочитано" not in response.text
    assert 'class="reading-progress' not in response.text


def test_add_is_idempotent(client: TestClient) -> None:
    _register(client)
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
        second = client.post("/library/6712--test-novel/add", follow_redirects=False)

    assert second.status_code == 303  # not an error to add twice


def test_add_unknown_title_is_not_found(client: TestClient) -> None:
    _register(client)
    exc = TitleNotFoundError("6712--missing")

    class _RaisingClient:
        async def __aenter__(self) -> "_RaisingClient":
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

        async def get_info(self) -> Title:
            raise exc

    with patch("app.services.client.RanobeLib", return_value=_RaisingClient()):
        response = client.post("/library/6712--missing/add")

    assert response.status_code == 404


def test_add_honors_custom_next(client: TestClient) -> None:
    _register(client)
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.post(
            "/library/6712--test-novel/add",
            data={"next": "/library"},
            follow_redirects=False,
        )

    assert response.headers["location"] == "/library"


def test_add_rejects_open_redirect_next(client: TestClient) -> None:
    _register(client)
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.post(
            "/library/6712--test-novel/add",
            data={"next": "https://evil.example"},
            follow_redirects=False,
        )

    assert response.headers["location"] == "/titles/6712--test-novel"


def test_add_rejects_protocol_relative_next(client: TestClient) -> None:
    _register(client)
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.post(
            "/library/6712--test-novel/add",
            data={"next": "//evil.example"},
            follow_redirects=False,
        )

    assert response.headers["location"] == "/titles/6712--test-novel"


def test_show_library_anonymous_is_viewable_but_prompts_to_log_in(client: TestClient) -> None:
    response = client.get("/library")

    assert response.status_code == 200
    assert 'href="/login"' in response.text
    assert 'href="/register"' in response.text
    assert 'href="/library/catalog"' in response.text  # locked-state CTA (PR 15)


def test_show_library_empty_state(client: TestClient) -> None:
    _register(client)

    response = client.get("/library")

    assert response.status_code == 200
    assert "Пока пусто" in response.text


def test_show_library_lists_added_titles_with_progress(client: TestClient) -> None:
    _register(client)
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")

    # Progress recording itself is covered by tests/test_chapters.py - here just check
    # the library page reflects it once it's there.
    record_progress(
        get_connection(), user_id=1, slug_url="6712--test-novel", volume="1", number="5"
    )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/library")

    assert response.status_code == 200
    assert "Test Novel" in response.text
    assert "Том 1, глава 5" in response.text


def test_show_library_renders_reading_progress_bar(client: TestClient) -> None:
    _register(client)
    title = _fake_title()
    volumes = [
        Volume(
            number="1",
            chapters=[Chapter(id=i, volume="1", number=str(i)) for i in range(1, 5)],
        )
    ]

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        client.post("/library/6712--test-novel/add")

    record_progress(
        get_connection(), user_id=1, slug_url="6712--test-novel", volume="1", number="2"
    )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        response = client.get("/library")

    assert response.status_code == 200
    assert 'class="reading-progress"' in response.text
    assert 'style="width: 50%"' in response.text  # 2 of 4 chapters


def test_show_library_omits_progress_bar_for_unopened_titles(client: TestClient) -> None:
    _register(client)
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
        response = client.get("/library")

    assert response.status_code == 200
    assert "Ещё не начали читать" in response.text
    assert 'class="reading-progress"' not in response.text


def test_show_library_prefers_russian_name(client: TestClient) -> None:
    _register(client)
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
        client.post("/library/6712--test-novel/add")
        response = client.get("/library")

    assert response.status_code == 200
    assert "Тестовый роман" in response.text
    assert "Test Novel" not in response.text


def test_show_library_renders_cover(client: TestClient) -> None:
    _register(client)
    title = _fake_title(cover=Cover(default="https://example.com/cover.jpg"))

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
        response = client.get("/library")

    assert 'src="https://example.com/cover.jpg"' in response.text


def test_show_library_survives_one_unreachable_title(client: TestClient) -> None:
    _register(client)
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")

    class _RaisingClient:
        async def __aenter__(self) -> "_RaisingClient":
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

        async def get_info(self) -> Title:
            raise TitleNotFoundError("6712--test-novel")

    with patch("app.services.client.RanobeLib", return_value=_RaisingClient()):
        response = client.get("/library")

    assert response.status_code == 200
    assert "6712--test-novel" in response.text


def test_add_by_url_resolves_and_adds(client: TestClient) -> None:
    _register(client)
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.post(
            "/library/add",
            data={"url": "https://ranobelib.me/ru/book/6712--test-novel"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/titles/6712--test-novel"


def test_add_by_url_rejects_unparseable_input(client: TestClient) -> None:
    _register(client)

    response = client.post("/library/add", data={"url": "not a link at all"})

    assert response.status_code == 400
    assert "Не удалось распознать ссылку" in response.text


def test_add_by_url_requires_login(client: TestClient) -> None:
    response = client.post(
        "/library/add", data={"url": "https://ranobelib.me/ru/book/6712--x"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"

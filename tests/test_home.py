from collections.abc import Iterator
from json import dumps
from unittest.mock import patch
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from ranobelib.models import Chapter, Cover, Label, Title, Volume

from app.main import app

client = TestClient(app)


def test_home_renders_search_form() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert 'action="/titles/open"' in response.text
    assert 'name="url"' in response.text


def _set_recent_cookie(test_client: TestClient, slug_url: str, name: str) -> None:
    test_client.cookies.set(
        "recent_titles",
        quote(dumps([{"slug_url": slug_url, "name": name}])),
    )


def _fake_title(slug_url: str = "6712--test-novel", name: str = "Test Novel") -> Title:
    return Title(
        id=6712,
        name=name,
        slug=slug_url.split("--", 1)[1],
        slug_url=slug_url,
        cover=Cover(),
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


@pytest.fixture
def db_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """PR 68's progress lookup needs a real logged-in user with a library entry, so
    unlike the plain `client` above this needs the app's DB - same isolation strategy as
    tests/test_api_library.py: the shared test Postgres database is wiped and re-migrated
    per test, with an explicit `with TestClient(app) as client:` so app.main's lifespan
    (migrations) actually runs."""
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")

    from app.config import get_settings
    from tests.db_reset import reset_app_database

    reset_app_database(monkeypatch)
    get_settings.cache_clear()

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


def _register(test_client: TestClient, email: str = "alice@example.com") -> None:
    test_client.post(
        "/register",
        data={"email": email, "password": "hunter2pass", "password_confirm": "hunter2pass"},
    )


def test_home_omits_progress_for_anonymous_visitor(db_client: TestClient) -> None:
    """No account, so nothing to match the recent-titles cookie against (PR 68)."""
    _set_recent_cookie(db_client, "6712--test-novel", "Test Novel")

    response = db_client.get("/")

    assert response.status_code == 200
    assert "Test Novel" in response.text
    assert 'class="reading-progress"' not in response.text


def test_home_omits_progress_when_title_not_in_library(db_client: TestClient) -> None:
    """Logged in, but this particular recent title was only opened from its description
    page and never added to the personal library - still nothing to show."""
    _register(db_client)
    title = _fake_title()
    _set_recent_cookie(db_client, "6712--test-novel", "Test Novel")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = db_client.get("/")

    assert response.status_code == 200
    assert 'class="reading-progress"' not in response.text


def test_home_omits_progress_when_never_read(db_client: TestClient) -> None:
    """In the library, but no chapter opened yet - PR 27's own rule for "Читаю" applies
    here the same way: no recorded position, no percentage to show."""
    _register(db_client)
    title = _fake_title()
    _set_recent_cookie(db_client, "6712--test-novel", "Test Novel")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        db_client.post("/library/6712--test-novel/add")
        response = db_client.get("/")

    assert response.status_code == 200
    assert 'class="reading-progress"' not in response.text


async def test_home_shows_progress_bar_for_logged_in_user_with_recorded_progress(
    db_client: TestClient,
) -> None:
    from app.db.connection import connection
    from app.db.library import record_progress

    _register(db_client)
    title = _fake_title()
    volumes = [
        Volume(
            number="1",
            chapters=[Chapter(id=i, volume="1", number=str(i)) for i in range(1, 5)],
        )
    ]
    _set_recent_cookie(db_client, "6712--test-novel", "Test Novel")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        db_client.post("/library/6712--test-novel/add")

    async with connection() as conn:
        await record_progress(
            conn, user_id=1, slug_url="6712--test-novel", volume="1", number="3"
        )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        response = db_client.get("/")

    assert response.status_code == 200
    assert 'class="reading-progress"' in response.text
    assert 'style="width: 75%"' in response.text  # 3 of 4 chapters

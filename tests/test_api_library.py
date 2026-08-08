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
from ranobelib.models import Cover, Label, Title, Volume

import app.db.connection as db_connection
from app.config import get_settings


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


class _FakeClient:
    def __init__(self, title: Title) -> None:
        self._title = title

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_info(self) -> Title:
        return self._title

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

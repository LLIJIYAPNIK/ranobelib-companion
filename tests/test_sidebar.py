from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.db.connection as db_connection
from app.config import get_settings
from app.main import app

client = TestClient(app)


@pytest.fixture
def logged_in_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    get_settings.cache_clear()
    db_connection._connection = None

    with TestClient(app) as test_client:
        test_client.post(
            "/register",
            data={
                "email": "alice.wong@example.com",
                "password": "hunter2pass",
                "password_confirm": "hunter2pass",
            },
        )
        yield test_client

    get_settings.cache_clear()
    db_connection._connection = None


def test_sidebar_renders_a_collapsed_burger_toggle_by_default() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert 'data-role="sidebar"' in response.text
    assert 'data-role="sidebar-toggle"' in response.text
    assert 'aria-expanded="false"' in response.text
    assert "static/js/sidebar-toggle.js" in response.text


def test_sidebar_renders_a_text_label_next_to_each_nav_icon() -> None:
    response = client.get("/")

    assert response.status_code == 200
    for label in ("Главная", "Библиотека", "Загрузки", "Активность", "Настройки"):
        assert f'<span class="sidebar__label">{label}</span>' in response.text


def test_logged_in_visitor_sees_an_avatar_with_initials_linking_to_settings(
    logged_in_client: TestClient,
) -> None:
    response = logged_in_client.get("/")

    assert response.status_code == 200
    assert 'class="sidebar__avatar"' in response.text
    assert 'href="/settings"' in response.text
    assert 'title="alice.wong@example.com"' in response.text
    assert ">AW</a>" in response.text
    assert "sidebar__account-logout" not in response.text


def test_logged_in_visitor_no_longer_sees_a_direct_logout_button(
    logged_in_client: TestClient,
) -> None:
    response = logged_in_client.get("/")

    assert 'action="/logout"' not in response.text
    assert ">Выйти</" not in response.text

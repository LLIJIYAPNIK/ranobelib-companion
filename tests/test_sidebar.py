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


def test_logged_in_visitor_sees_a_profile_menu_trigger_avatar(
    logged_in_client: TestClient,
) -> None:
    response = logged_in_client.get("/")

    assert response.status_code == 200
    assert 'data-role="profile-menu"' in response.text
    assert 'data-role="profile-menu-trigger"' in response.text
    assert 'aria-haspopup="true"' in response.text
    assert 'aria-expanded="false"' in response.text
    assert 'title="alice.wong@example.com"' in response.text
    assert ">AW</button>" in response.text
    assert "static/js/profile-menu.js" in response.text


def test_anonymous_visitor_gets_no_profile_menu() -> None:
    response = client.get("/")

    assert 'data-role="profile-menu"' not in response.text
    assert "static/js/profile-menu.js" not in response.text

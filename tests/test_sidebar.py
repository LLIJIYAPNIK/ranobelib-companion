from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from tests.db_reset import reset_app_database

client = TestClient(app)


@pytest.fixture
def logged_in_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

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


def test_sidebar_renders_a_collapsed_burger_toggle_by_default() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert 'data-role="sidebar"' in response.text
    assert 'data-role="sidebar-toggle"' in response.text
    assert 'aria-expanded="false"' in response.text
    assert "static/js/sidebar-toggle.js" in response.text


def test_sidebar_applies_saved_expanded_state_synchronously_before_first_paint() -> None:
    # PR 108: this must be a non-deferred script running while the <nav> is being parsed
    # (not the deferred sidebar-toggle.js), so the saved state is applied before the
    # sidebar's first paint and its width transition never plays on a plain page
    # navigation. Moved from an inline <script> into its own file in PR 189 (script-src
    # 'self', no 'unsafe-inline'), so the check here moved from content to placement/lack
    # of a defer attribute - the actual localStorage logic is covered by
    # test_security_headers.py's static-file content check.
    response = client.get("/")

    assert response.status_code == 200
    nav_start = response.text.index('<nav class="sidebar" data-role="sidebar">')
    toggle_start = response.text.index('data-role="sidebar-toggle"')
    inline_script = response.text[nav_start:toggle_start]
    assert "<script src=" in inline_script
    assert "static/js/sidebar-expand-init.js" in inline_script


def test_sidebar_renders_a_text_label_next_to_each_nav_icon() -> None:
    response = client.get("/")

    assert response.status_code == 200
    for label in ("Главная", "Библиотека", "Загрузки", "Активность", "Друзья", "Настройки"):
        assert f'<span class="sidebar__label">{label}</span>' in response.text


def test_sidebar_friends_link_points_at_the_friends_page() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/friends"' in response.text


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


def test_profile_menu_trigger_shows_the_uploaded_avatar_image_over_initials(
    logged_in_client: TestClient,
) -> None:
    logged_in_client.post(
        "/settings/account/avatar",
        files={"avatar": ("me.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png")},
    )

    response = logged_in_client.get("/")

    assert response.status_code == 200
    assert '<img class="avatar-img" src="/avatars/' in response.text
    assert ">AW</button>" not in response.text


def test_anonymous_visitor_gets_no_profile_menu() -> None:
    response = client.get("/")

    assert 'data-role="profile-menu"' not in response.text
    assert "static/js/profile-menu.js" not in response.text


def test_profile_menu_links_to_profile_library_and_settings(
    logged_in_client: TestClient,
) -> None:
    response = logged_in_client.get("/")

    assert response.status_code == 200
    for label, href in (
        ("Профиль", "/profile"),
        ("Читаю", "/library"),
        ("Настройки", "/settings"),
    ):
        assert f'<a class="profile-menu__item" href="{href}">{label}</a>' in response.text


def test_profile_menu_lets_you_log_out(logged_in_client: TestClient) -> None:
    response = logged_in_client.get("/")

    assert response.status_code == 200
    assert '<form class="profile-menu__form" method="post" action="/logout">' in response.text
    assert '<button class="profile-menu__item" type="submit">Выйти</button>' in response.text

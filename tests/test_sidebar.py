import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from tests.auth_helpers import register
from tests.db_reset import reset_app_database

client = TestClient(app)


@pytest.fixture
def logged_in_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

    with TestClient(app) as test_client:
        register(test_client, "alice.wong@example.com")
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
    nav_start = response.text.index('<nav class="sidebar" data-role="sidebar"')
    toggle_start = response.text.index('data-role="sidebar-toggle"')
    inline_script = response.text[nav_start:toggle_start]
    assert "<script src=" in inline_script
    assert "static/js/sidebar-expand-init.js" in inline_script


def test_sidebar_main_list_is_home_library_downloads_only() -> None:
    # PR 249 (Aurora Ink): Активность/Друзья/Настройки moved into the Account hub, so the
    # main list - desktop rail and mobile Quiet Edge Bar alike - is just these three.
    response = client.get("/")

    assert response.status_code == 200
    nav = response.text[response.text.index('class="sidebar__nav"') : response.text.index(
        'class="sidebar__account"'
    )]
    for label in ("Главная", "Библиотека", "Загрузки"):
        assert f'<span class="sidebar__label">{label}</span>' in nav
    for label in ("Активность", "Друзья", "Настройки", "Уведомления"):
        assert label not in nav


def test_guest_quiet_edge_bar_shows_catalog_and_hides_downloads_on_mobile() -> None:
    # Quiet Edge Bar for a guest is «Главная · Каталог»: the library link carries a
    # mobile-only «Каталог» label and Загрузки is desktop-only.
    response = client.get("/")

    assert '<span class="sidebar__label-mobile">Каталог</span>' in response.text
    assert "sidebar__link--desktop-only" in response.text


def test_logged_in_quiet_edge_bar_keeps_downloads_on_mobile(
    logged_in_client: TestClient,
) -> None:
    response = logged_in_client.get("/")

    assert "sidebar__link--desktop-only" not in response.text
    assert "sidebar__label-mobile" not in response.text


def test_active_nav_link_is_marked_current() -> None:
    response = client.get("/")

    assert (
        '<a class="sidebar__link sidebar__link--active" href="/" aria-current="page">'
        in response.text
    )


def test_account_hub_friends_link_points_at_the_friends_page(
    logged_in_client: TestClient,
) -> None:
    response = logged_in_client.get("/")

    assert response.status_code == 200
    assert 'href="/friends"' in response.text


def test_sidebar_wires_the_mobile_account_strip_script() -> None:
    # PR 249: mobile-account-strip.js now only drives the top strip's scroll background;
    # the container it used to reparent into stays, rendered for every visitor.
    response = client.get("/")

    assert response.status_code == 200
    assert 'data-role="sidebar-account-actions"' in response.text
    assert "static/js/mobile-account-strip.js" in response.text


def test_notifications_bell_is_rendered_inside_the_account_actions(
    logged_in_client: TestClient,
) -> None:
    # PR 249: the bell lives in .sidebar__account from the start (desktop rail footer and
    # mobile top strip alike) instead of being reparented there by JS on mobile.
    response = logged_in_client.get("/")

    assert response.status_code == 200
    actions = response.text.index('data-role="sidebar-account-actions"')
    assert actions < response.text.index('data-role="notifications-trigger"')
    assert response.text.index('data-role="notifications-panel"') < response.text.index(
        'data-role="profile-menu"'
    )


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
    # PR 223 (Claude Design import): the avatar circle is a plain span nested inside
    # .sidebar__account-trigger (the real button), not the button itself - see its own
    # accompanying name/email/chevron, also inside the trigger.
    assert '<span class="sidebar__avatar" aria-hidden="true">AW</span>' in response.text
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
    assert '<span class="sidebar__avatar" aria-hidden="true">AW</span>' not in response.text


def test_anonymous_visitor_gets_no_profile_menu() -> None:
    response = client.get("/")

    assert 'data-role="profile-menu"' not in response.text
    assert "static/js/profile-menu.js" not in response.text


def test_profile_menu_is_the_account_hub(
    logged_in_client: TestClient,
) -> None:
    # PR 249 (Aurora Ink): Профиль · Активность · Друзья · Настройки · Выйти; «Читаю» is
    # gone, Настройки carries data-role="settings-link" now that it lives here.
    response = logged_in_client.get("/")

    assert response.status_code == 200
    panel = response.text[response.text.index('data-role="profile-menu-panel"') :]
    assert '<a class="profile-menu__head" href="/profile">' in panel
    for href, label in (("/activity", "Активность"), ("/friends", "Друзья")):
        assert re.search(rf'<a class="profile-menu__item" href="{href}">.*?</svg>{label}</a>', panel)
    assert re.search(
        r'<a class="profile-menu__item" href="/settings" data-role="settings-link">.*?</svg>Настройки</a>',
        panel,
    )
    assert "Читаю" not in panel[: panel.index("</form>")]


def test_account_hub_marks_the_current_section(logged_in_client: TestClient) -> None:
    response = logged_in_client.get("/settings/reading")

    assert (
        '<a class="profile-menu__item" href="/settings" data-role="settings-link" aria-current="page">'
        in response.text
    )


def test_profile_menu_lets_you_log_out(logged_in_client: TestClient) -> None:
    response = logged_in_client.get("/")

    assert response.status_code == 200
    assert '<form class="profile-menu__form" method="post" action="/logout">' in response.text
    assert re.search(
        r'<button class="profile-menu__item profile-menu__item--danger" type="submit">.*?</svg>Выйти</button>',
        response.text,
    )
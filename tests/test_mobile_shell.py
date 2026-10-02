"""PR 279 (Webnovells Mobile, wave 34): the shared mobile shell - header, bottom
navigation and the one bottom sheet every screen reuses."""

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


def _header_title(html: str) -> str:
    match = re.search(r'<span class="sidebar__strip-title">(.*?)</span>', html, re.S)
    assert match, "the mobile header has no title slot"
    return match.group(1).strip()


def test_mobile_header_title_is_empty_on_home() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert _header_title(response.text) == ""


@pytest.mark.parametrize(
    ("path", "title"),
    [
        ("/library", "Библиотека"),
        ("/downloads", "Загрузки"),
        ("/activity", "Активность"),
        ("/friends", "Друзья"),
        ("/settings/reading", "Настройки"),
        ("/profile", "Профиль"),
    ],
)
def test_mobile_header_names_the_current_section(
    logged_in_client: TestClient, path: str, title: str
) -> None:
    response = logged_in_client.get(path)

    assert response.status_code == 200
    assert _header_title(response.text) == title


def test_mobile_header_marks_the_avatar_on_the_users_own_profile_only(
    logged_in_client: TestClient,
) -> None:
    own = logged_in_client.get("/profile")
    home = logged_in_client.get("/")

    assert "sidebar__account-trigger--current" in own.text
    assert "sidebar__account-trigger--current" not in home.text


def test_base_renders_one_empty_bottom_sheet_outside_the_app_shell() -> None:
    # bottom-sheet.js makes .app-shell inert while the sheet is open, so the sheet itself
    # has to live outside it.
    response = client.get("/")

    html = response.text
    assert html.count('data-role="bottom-sheet"') == 1
    sheet = html.index('data-role="bottom-sheet"')
    assert sheet > html.index('data-role="sidebar"')
    assert '<div class="main">' in html[: sheet]
    assert 'role="dialog" aria-modal="true" aria-labelledby="bottom-sheet-title"' in html
    assert 'data-role="bottom-sheet-close" aria-label="Закрыть"' in html
    assert re.search(r'data-role="bottom-sheet-body"></div>', html)


def test_bottom_sheet_script_loads_for_guests_and_before_the_profile_menu(
    logged_in_client: TestClient,
) -> None:
    guest = client.get("/")
    user = logged_in_client.get("/")

    assert "static/js/bottom-sheet.js" in guest.text
    assert user.text.index("static/js/bottom-sheet.js") < user.text.index(
        "static/js/profile-menu.js"
    )


def test_profile_menu_brings_no_grabber_of_its_own(logged_in_client: TestClient) -> None:
    # PR 279: the shared sheet draws the grabber; the hub panel is only its content.
    response = logged_in_client.get("/")

    assert "profile-menu__grabber" not in response.text


def test_bottom_sheet_script_follows_the_handoff_thresholds() -> None:
    # Mobile handoff.md -> Bottom sheet: the numbers are spec, not taste.
    script = (Path(__file__).parents[1] / "app/static/js/bottom-sheet.js").read_text(
        encoding="utf-8"
    )

    for constant in (
        "CLOSE_DISTANCE = 160",
        "CLOSE_SHARE = 0.33",
        "CLOSE_SPEED = 0.55",
        "SPEED_WINDOW = 100",
        "SPEED_PAUSE = 80",
        "BODY_SLOP = 6",
        "CLOSE_MS = 220",
    ):
        assert constant in script
    assert "{ passive: false }" in script
    assert "setPointerCapture" in script
    assert "prefers-reduced-motion: reduce" in script


def test_no_global_horizontal_overflow_clipping() -> None:
    # Mobile handoff.md -> Общие правила: no global overflow-x:hidden - a page that
    # overflows sideways must be fixed where it overflows, and only explicit ribbons
    # (.wn-ribbon) scroll horizontally.
    css = (Path(__file__).parents[1] / "app/static/css/app.css").read_text(encoding="utf-8")

    for selector in ("html", "body", ".app-shell", ".main"):
        for block in re.findall(
            rf"(?m)^\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", css
        ):
            assert "overflow-x: hidden" not in block, selector
            assert "overflow: hidden" not in block, selector


def test_keyboard_script_hides_the_bottom_bar_only() -> None:
    # The bar hides at a 140px+ visual-viewport shortfall; the header shares .sidebar, so
    # the CSS must hide .sidebar__nav, never the whole <nav>.
    root = Path(__file__).parents[1] / "app/static"
    script = (root / "js/mobile-keyboard.js").read_text(encoding="utf-8")
    css = (root / "css/app.css").read_text(encoding="utf-8")

    assert "KEYBOARD_MIN = 140" in script
    assert re.search(r"html\.keyboard-open \.sidebar__nav \{\s*display: none;", css)
    assert not re.search(r"html\.keyboard-open \.sidebar \{[^}]*display: none", css)
    assert "static/js/mobile-keyboard.js" in client.get("/").text

"""Phones: the sidebar as a drawer from the left instead of a bottom bar - the top bar's
menu button, the drawer's ✕ and scrim, the account and Настройки at its foot."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
JS = (ROOT / "app/static/js/sidebar-drawer.js").read_text(encoding="utf-8")
PHONE = CSS.split("/* Mobile: a top bar and the sidebar as a drawer from the left", 1)[1]
PHONE = PHONE.split("\n}\n", 1)[0]

client = TestClient(app)


def _rule(selector: str) -> str:
    match = re.search(rf"\n  {re.escape(selector)} \{{([^}}]*)\}}", PHONE)
    assert match, selector
    return match.group(1)


def test_the_page_renders_the_drawer_controls_and_no_bottom_bar_script() -> None:
    html = client.get("/").text

    assert '<nav class="sidebar" id="site-nav" data-role="sidebar"' in html
    assert (
        'data-role="sidebar-drawer-open" aria-controls="site-nav" aria-expanded="false"'
        ' aria-label="Открыть меню"' in html
    )
    assert 'data-role="sidebar-drawer-close" aria-label="Закрыть меню"' in html
    assert '<div class="sidebar-scrim" data-role="sidebar-scrim" hidden></div>' in html
    assert "static/js/sidebar-drawer.js" in html
    assert "mobile-navbar-swipe.js" not in html
    assert not (ROOT / "app/static/js/mobile-navbar-swipe.js").exists()


def test_a_guest_signs_in_from_the_drawer_too() -> None:
    html = client.get("/").text

    me = html[html.index('class="sidebar__me"') : html.index("</nav>")]
    assert 'href="/settings" data-role="settings-link"' in me
    assert (
        '<a class="sidebar__me-login" href="/login" data-role="auth-modal-trigger">'
        "Войти или создать аккаунт</a>" in me
    )


def test_the_drawer_slides_from_the_left_and_hides_when_closed() -> None:
    drawer = _rule(".sidebar")
    assert "left: calc(-1 * var(--drawer-width) - 32px);" in drawer
    assert "visibility: hidden;" in drawer  # nothing in it takes focus off-screen
    assert "transform" not in drawer  # the top bar is its fixed child
    opened = _rule(".sidebar--open")
    assert "left: 0;" in opened
    assert "visibility: visible;" in opened
    # The top bar stays on screen while the drawer is closed, and steps aside when open.
    assert "visibility: visible;" in _rule(".sidebar__account")
    assert "visibility: hidden;" in _rule(".sidebar--open .sidebar__account")


def test_nothing_is_reserved_for_a_bottom_bar() -> None:
    root = re.search(r"\n  :root,\n  body:has\(\.sidebar--expanded\) \{([^}]*)\}", PHONE)
    assert root
    assert "--shell-bottom: env(safe-area-inset-bottom, 0px);" in root.group(1)
    assert "padding-bottom: calc(var(--shell-bottom) + 32px);" in _rule(".main")
    assert "flex-direction: row;" not in _rule(".sidebar__nav")


def test_rows_and_controls_meet_the_touch_floor() -> None:
    assert "height: 52px;" in _rule(".sidebar__link")
    for selector in (".sidebar__menu", ".sidebar__close", ".sidebar__bell"):
        rule = _rule(selector)
        assert "width: 44px;" in rule and "height: 44px;" in rule, selector
    assert "min-height: 44px;" in _rule(".sidebar__guest")


def test_the_script_traps_focus_and_closes_every_way() -> None:
    assert 'sidebar.classList.add("sidebar--open")' in JS
    assert 'openButton.setAttribute("aria-expanded", "true")' in JS
    assert "main.inert = true" in JS
    assert 'event.key === "Escape"' in JS
    assert 'event.key !== "Tab"' in JS
    assert 'scrim.addEventListener("click", () => close())' in JS
    assert "dx < -SWIPE_CLOSE" in JS
    # The account menu's bottom sheet over the drawer keeps Escape and Tab for itself.
    assert "window.bottomSheet?.isOpen()" in JS
    assert "if (!phone.matches) close({ restoreFocus: false });" in JS

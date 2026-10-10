"""PR 341: the admin panel's shell - sidebar, top bar, ADMIN_TIMEZONE - plus the two
guarantees every admin page keeps: nothing loads from an outside CDN (CSP), and every
route but the login form sits behind ``require_admin``."""

import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.api.admin as admin_api
from app.auth.admin import require_admin
from app.config import get_settings
from tests.test_admin_auth import PASSWORD, Clock, _client, _login
from tests.test_admin_routes import _routes

# The only outside hosts an admin page may reference - the site's own fonts, which CSP
# allows for styles/fonts (app/security_headers.py). Never a script.
ALLOWED_EXTERNAL_HOSTS = {"fonts.googleapis.com", "fonts.gstatic.com"}
ADMIN_PAGES = ("/admin", "/admin/tables", "/admin/tables/users")


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    import app.auth.admin as admin_auth

    fake = Clock()
    monkeypatch.setattr(admin_auth, "_now", fake)
    monkeypatch.setattr(admin_auth, "_failures", {})
    return fake


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock) -> Iterator[TestClient]:
    with _client(monkeypatch, tmp_path, ADMIN_PASSWORD=PASSWORD) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    assert _login(client).status_code == 303
    return client


def _sidebar(html: str) -> str:
    return re.search(r'<aside class="wn-admin-side".*?</aside>', html, re.S).group(0)


# --- sidebar and top bar ------------------------------------------------------------------


def test_the_sidebar_lists_the_waves_screens_in_their_groups(admin: TestClient) -> None:
    side = _sidebar(admin.get("/admin").text)

    groups = re.findall(r'<p class="wn-admin-side__group">([^<]+)</p>', side)
    assert groups == ["Контент", "Сообщество", "Данные", "Платформа"]
    labels = re.findall(r'<span class="wn-admin-side__label">([^<]+)</span>', side)
    assert labels == [
        "Обзор",
        "Аналитика",
        "Пользователи",
        "Комментарии",
        "Данные БД",
        "Система",
        "Журнал",
    ]


@pytest.mark.parametrize(
    ("path", "active"),
    [
        ("/admin", "/admin"),
        ("/admin/tables", "/admin/tables"),
        ("/admin/tables/users", "/admin/tables"),
    ],
)
def test_the_current_screen_is_marked(admin: TestClient, path: str, active: str) -> None:
    side = _sidebar(admin.get(path).text)

    current = re.findall(
        r'<a class="wn-admin-side__item" href="([^"]+)"[^>]*aria-current="page"', side
    )
    assert current == [active]


def test_screens_from_later_prs_are_shown_but_not_links(admin: TestClient) -> None:
    side = _sidebar(admin.get("/admin").text)

    for item in admin_api.ADMIN_NAV:
        if item.href is None:
            assert f'title="{item.label} — появится позже"' in side
            assert 'aria-disabled="true"' in side
    hrefs = re.findall(r'href="(/admin[^"]*)"', side)
    assert sorted(hrefs) == ["/admin", "/admin/tables"]


def test_the_account_block_is_just_the_admin_and_logout(admin: TestClient) -> None:
    html = admin.get("/admin").text
    side = _sidebar(html)

    assert "Администратор" in side
    assert "Дмитрий" not in html
    token = re.search(r'name="csrf" value="([^"]+)"', side).group(1)
    assert '<form class="wn-admin-side__logout" method="post" action="/admin/logout">' in side
    assert (
        admin.post("/admin/logout", data={"csrf": token}, follow_redirects=False).status_code == 303
    )


def test_the_top_bar_has_the_page_title_and_a_way_back_to_the_site(admin: TestClient) -> None:
    html = admin.get("/admin/tables/users").text

    assert '<h1 class="wn-admin-top__title" id="admin-page-title">users</h1>' in html
    assert html.count("<h1") == 1
    assert '<a class="wn-admin-top__site" href="/"' in html
    assert 'data-role="admin-search-slot"' in html


def test_the_slide_out_sidebar_is_wired(admin: TestClient) -> None:
    html = admin.get("/admin").text

    assert 'data-role="admin-nav-open" aria-controls="admin-sidebar" aria-expanded="false"' in html
    assert 'data-role="admin-nav-close"' in html
    assert 'data-role="admin-nav-scrim" hidden' in html
    assert "js/admin-sidebar.js" in html


def test_the_login_page_has_no_shell(client: TestClient) -> None:
    html = client.get("/admin/login").text

    assert "wn-admin-side" not in html
    assert "admin-sidebar.js" not in html
    assert 'action="/admin/login"' in html


# --- ADMIN_TIMEZONE -----------------------------------------------------------------------


def _freeze(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    monkeypatch.setattr(
        admin_api,
        "_server_now",
        lambda: moment.astimezone(ZoneInfo(get_settings().admin_timezone)),
    )


def test_the_top_bar_shows_the_server_time_in_utc_by_default(
    admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, datetime(2026, 10, 9, 22, 30, tzinfo=UTC))

    html = admin.get("/admin").text

    assert '<time datetime="2026-10-09T22:30+00:00">' in html
    assert "Пятница, 9 октября 2026 · </span>22:30" in html
    assert '<span class="wn-admin-top__tz">UTC</span>' in html
    assert "МСК" not in html


def test_admin_timezone_moves_the_date_and_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock
) -> None:
    with _client(
        monkeypatch, tmp_path, ADMIN_PASSWORD=PASSWORD, ADMIN_TIMEZONE="Asia/Tokyo"
    ) as client:
        _freeze(monkeypatch, datetime(2026, 10, 9, 22, 30, tzinfo=UTC))
        _login(client)
        html = client.get("/admin").text
    get_settings.cache_clear()

    # 22:30 UTC on Friday is already Saturday morning in Tokyo.
    assert '<time datetime="2026-10-10T07:30+09:00">' in html
    assert "Суббота, 10 октября 2026 · </span>07:30" in html
    assert '<span class="wn-admin-top__tz">Asia/Tokyo</span>' in html


@pytest.mark.parametrize(("value", "expected"), [("", "UTC"), ("Europe/Moscow", "Europe/Moscow")])
def test_admin_timezone_setting(monkeypatch: pytest.MonkeyPatch, value: str, expected: str) -> None:
    monkeypatch.setenv("ADMIN_TIMEZONE", value)
    get_settings.cache_clear()
    try:
        assert get_settings().admin_timezone == expected
    finally:
        get_settings.cache_clear()


def test_an_unknown_admin_timezone_falls_back_to_utc_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ADMIN_TIMEZONE", "Mars/Olympus_Mons")
    get_settings.cache_clear()
    try:
        with caplog.at_level(logging.WARNING, logger="app.config"):
            assert get_settings().admin_timezone == "UTC"
    finally:
        get_settings.cache_clear()
    assert "Mars/Olympus_Mons" in caplog.text


# --- no outside CDN, require_admin everywhere ---------------------------------------------


def _references(html: str) -> list[tuple[str, str]]:
    return re.findall(r'<(script|link|img|iframe|source)\b[^>]*?\b(?:src|href)="([^"]+)"', html)


@pytest.mark.parametrize("path", ["/admin/login", *ADMIN_PAGES])
def test_admin_pages_load_nothing_from_an_outside_cdn(admin: TestClient, path: str) -> None:
    html = admin.get(path).text if path != "/admin/login" else _logged_out_login(admin)

    references = _references(html)
    assert references
    for tag, url in references:
        parsed = urlparse(url)
        # url_for() writes the page's own origin into static URLs - that's 'self'.
        same_origin = parsed.netloc in ("", "testserver")
        if tag == "script":
            assert same_origin and parsed.path.startswith("/static/"), url
        else:
            assert same_origin or parsed.netloc in ALLOWED_EXTERNAL_HOSTS, url
    # Inline scripts would be blocked by script-src 'self' anyway - none at all.
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    for cdn in ("unpkg.com", "jsdelivr", "cdnjs", "lucide"):
        assert cdn not in html


def _logged_out_login(client: TestClient) -> str:
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/admin").text).group(1)
    client.post("/admin/logout", data={"csrf": token})
    return client.get("/admin/login").text


def _dependency_calls(dependant) -> Iterator:
    for sub in dependant.dependencies:
        yield sub.call
        yield from _dependency_calls(sub)


def test_every_admin_route_but_the_login_form_requires_the_admin() -> None:
    from app.main import app

    routes = [
        route
        for route in _routes(app.routes)
        if isinstance(route, APIRoute) and re.match(r"^/admin(/|$)", route.path)
    ]
    assert routes
    for route in routes:
        protected = require_admin in set(_dependency_calls(route.dependant))
        if route.path == "/admin/login":
            assert not protected
        elif route.path == "/admin/logout":
            # Logging out needs only the CSRF token - it can't reveal or change anything.
            continue
        else:
            assert protected, route.path

"""PR 333: one account's responses never end up where another person on the device - or a
shared cache on the way - could get them (app/static_assets.py install_cache_policy(),
POST /logout in app/api/auth.py). The device's own storage - the sync queue, reading
positions, downloads - is cleared client-side (device-account.js, see
tests/test_device_account_js.py)."""

import re
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.static_assets import NO_CACHE, PRIVATE_NO_CACHE
from tests.auth_helpers import register
from tests.db_reset import reset_app_database


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


@pytest.mark.parametrize("path", ["/settings", "/activity", "/notifications/unread-count"])
def test_a_signed_in_accounts_responses_are_private(client: TestClient, path: str) -> None:
    register(client, "alice@example.com", nickname="alice")

    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == PRIVATE_NO_CACHE
    assert "Cookie" in response.headers["Vary"]


def test_a_guests_page_is_revalidated(client: TestClient) -> None:
    response = client.get("/login")

    assert response.headers["Cache-Control"] == NO_CACHE


def test_a_routes_own_cache_control_wins(client: TestClient) -> None:
    # The worker and the manifest are the same for everyone - set by their routes.
    register(client, "alice@example.com")

    assert client.get("/service-worker.js").headers["Cache-Control"] == NO_CACHE
    assert client.get("/manifest.webmanifest").headers["Cache-Control"] == NO_CACHE


def test_public_content_is_left_public(client: TestClient) -> None:
    register(client, "alice@example.com")

    class _Upstream:
        status_code = 200
        content = b"\x89PNG"
        headers = {"content-type": "image/png"}

        def raise_for_status(self) -> None:
            pass

    class _Http:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_Http":
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def get(self, *args: object, **kwargs: object) -> _Upstream:
            return _Upstream()

    with patch("app.api.images.httpx.AsyncClient", _Http):
        response = client.get("/images/view", params={"url": "https://ranobelib.me/a.png"})

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "public, max-age=604800"


def test_static_files_keep_their_own_rules(client: TestClient) -> None:
    register(client, "alice@example.com")

    response = client.get("/static/css/app.css")

    assert response.headers["Cache-Control"] == NO_CACHE


def test_logout_drops_the_browsers_http_cache_but_not_the_downloads(
    client: TestClient,
) -> None:
    # "cache" only: "storage" would also wipe the downloads the visitor chose to keep.
    register(client, "alice@example.com")

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["Clear-Site-Data"] == '"cache"'


def test_signed_in_pages_know_whose_device_data_it_is(client: TestClient) -> None:
    # device-account.js runs after the queue and the downloads index it clears, all in
    # <head>, ahead of the page's own scripts; it knows this page's account.
    register(client, "alice@example.com")  # user id 1

    html = client.get("/").text

    head = html[: html.index("</head>")]
    assert head.index("js/sync-queue.js") < head.index("js/offline-store.js")
    assert head.index("js/offline-store.js") < head.index("js/device-account.js")
    assert re.search(r'js/device-account\.js\?v=\w+" data-user-id="1"', head)
    assert 'id="logout-offline-choice"' in html
    assert 'data-logout-keep="1"' in html
    assert 'data-logout-keep="0"' in html


def test_guest_pages_have_no_account_to_guard(client: TestClient) -> None:
    html = client.get("/").text

    assert "device-account.js" not in html
    assert "logout-offline-choice" not in html

"""The global cookie notice banner in base.html (PR 185) - shown to every visitor,
logged in or not, since both cookies this site sets (the session cookie and the
anonymous `recent_titles` history) are functional, not tied to having an account."""

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
                "email": "alice@example.com",
                "password": "hunter2pass",
                "password_confirm": "hunter2pass",
            },
        )
        yield test_client

    get_settings.cache_clear()
    db_connection._connection = None


def test_anonymous_visitor_gets_the_cookie_notice() -> None:
    response = client.get("/")

    assert 'data-role="cookie-notice"' in response.text
    assert 'data-role="cookie-notice-dismiss"' in response.text
    assert "Понятно" in response.text
    assert "static/js/cookie-notice.js" in response.text


def test_logged_in_visitor_also_gets_the_cookie_notice(
    logged_in_client: TestClient,
) -> None:
    response = logged_in_client.get("/")

    assert 'data-role="cookie-notice"' in response.text
    assert "static/js/cookie-notice.js" in response.text


def test_cookie_notice_is_not_hidden_by_default_server_side() -> None:
    """The dismissed state is only known client-side (localStorage) - the server
    always renders the banner without a `hidden` attribute; the inline script right
    after it is what hides it synchronously for a visitor who already dismissed it."""
    response = client.get("/")

    assert '<div class="cookie-notice" data-role="cookie-notice">' in response.text


def test_cookie_notice_inline_script_checks_local_storage_not_a_cookie() -> None:
    response = client.get("/")

    assert 'localStorage.getItem("cookieNoticeDismissed")' in response.text

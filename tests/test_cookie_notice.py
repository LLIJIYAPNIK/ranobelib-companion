"""The cookie notice and the visitor's choice (PR 185, reworked in PR 375).

The site sets only its own cookies: ``session`` and ``cookie_consent`` are necessary,
``recent_titles`` («Недавние») is optional and written only after «Принять все». The
notice shows until the visitor chooses - decided by the server, from the cookie_consent
cookie - with «Принять все» and «Только необходимые» as two equal buttons, and links to
«Настройки → Данные и cookie», where the whole list is and the choice can be changed."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from tests.auth_helpers import register
from tests.db_reset import reset_app_database

ROOT = Path(__file__).parent.parent
client = TestClient(app, follow_redirects=False)


@pytest.fixture(autouse=True)
def _fresh_cookies() -> Iterator[None]:
    client.cookies.clear()
    yield
    client.cookies.clear()


@pytest.fixture
def logged_in_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

    with TestClient(app) as test_client:
        register(test_client, "alice@example.com")
        yield test_client

    get_settings.cache_clear()



def test_accepting_all_sets_the_choice_for_a_year_httponly() -> None:
    response = client.post(
        "/cookies/consent", data={"choice": "all"}, headers={"X-Requested-With": "fetch"}
    )

    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert "cookie_consent=all" in cookie
    assert "Max-Age=31536000" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "recent_titles" not in cookie


def test_essential_only_deletes_the_history_already_there() -> None:
    client.cookies.set("recent_titles", "abc")
    response = client.post(
        "/cookies/consent", data={"choice": "essential"}, headers={"X-Requested-With": "fetch"}
    )

    cookies = response.headers.get_list("set-cookie")
    assert any(c.startswith("cookie_consent=essential") for c in cookies)
    assert any(c.startswith('recent_titles=""') and "Max-Age=0" in c for c in cookies)


def test_without_the_script_the_form_redirects_back_on_this_site_only() -> None:
    back = client.post("/cookies/consent", data={"choice": "all", "next": "/catalog?q=x"})
    assert back.status_code == 303
    assert back.headers["location"] == "/catalog?q=x"

    for evil in ("https://evil.example/", "//evil.example/", "/\\evil.example"):
        response = client.post("/cookies/consent", data={"choice": "all", "next": evil})
        assert response.headers["location"] == "/", evil


def test_an_unknown_choice_is_refused() -> None:
    response = client.post("/cookies/consent", data={"choice": "some"})

    assert response.status_code == 400
    assert "set-cookie" not in response.headers

"""PR 218: GET/POST /login and /register optionally render just the bare _auth_card.html
fragment instead of the full page - app/api/auth.py's _is_modal_request()/_auth_template()
branch on the X-Requested-With header, the same signal auth-modal.js's fetch() calls will
send (see the next commit) to load/refresh the login-or-register modal without a full page
navigation. Exercised here directly over HTTP, independent of any JS.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from tests.db_reset import reset_app_database

_AJAX_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


def _register(client: TestClient, email: str, headers: dict[str, str] | None = None) -> object:
    return client.post(
        "/register",
        data={"email": email, "password": "hunter2pass", "password_confirm": "hunter2pass"},
        headers=headers,
        follow_redirects=False,
    )


def test_show_login_full_page_includes_sidebar(client: TestClient) -> None:
    response = client.get("/login")

    assert response.status_code == 200
    assert "sidebar" in response.text
    assert 'action="/login"' in response.text


def test_show_login_modal_fragment_omits_sidebar(client: TestClient) -> None:
    response = client.get("/login", headers=_AJAX_HEADERS)

    assert response.status_code == 200
    assert "sidebar" not in response.text
    assert 'action="/login"' in response.text


def test_show_register_modal_fragment_shows_register_form(client: TestClient) -> None:
    response = client.get("/register", headers=_AJAX_HEADERS)

    assert response.status_code == 200
    assert "sidebar" not in response.text
    assert 'action="/register"' in response.text
    assert "Никнейм" in response.text


def test_login_error_modal_fragment_omits_sidebar_but_shows_error(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "nobody@example.com", "password": "wrong"},
        headers=_AJAX_HEADERS,
    )

    assert response.status_code == 400
    assert "sidebar" not in response.text
    assert "Неверный email или пароль" in response.text
    # The submitted email is preserved, same as the full-page form.
    assert 'value="nobody@example.com"' in response.text


def test_login_error_full_page_still_renders_the_whole_page(client: TestClient) -> None:
    response = client.post(
        "/login", data={"email": "nobody@example.com", "password": "wrong"}
    )

    assert response.status_code == 400
    assert "sidebar" in response.text
    assert "Неверный email или пароль" in response.text


def test_register_error_modal_fragment_omits_sidebar(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.post(
        "/register",
        data={
            "email": "alice@example.com",
            "password": "hunter2pass",
            "password_confirm": "hunter2pass",
        },
        headers=_AJAX_HEADERS,
    )

    assert response.status_code == 400
    assert "sidebar" not in response.text
    assert "уже зарегистрирован" in response.text


def test_register_success_no_ajax_still_renders_avatar_prompt_directly(
    client: TestClient,
) -> None:
    # Unchanged PR 106 behavior - a plain, no-JS registration never redirects.
    response = _register(client, "alice@example.com")

    assert response.status_code == 200
    assert "Хотите добавить аватар?" in response.text


def test_register_success_via_modal_redirects_to_register_avatar(client: TestClient) -> None:
    response = _register(client, "alice@example.com", headers=_AJAX_HEADERS)

    assert response.status_code == 303
    assert response.headers["location"] == "/register/avatar"


def test_register_success_via_modal_session_is_set(client: TestClient) -> None:
    _register(client, "alice@example.com", headers=_AJAX_HEADERS)

    # The redirect's target is itself a protected page - reaching it (rather than being
    # bounced to /login) confirms the session was set before the redirect was issued.
    response = client.get("/register/avatar")

    assert response.status_code == 200
    assert "Хотите добавить аватар?" in response.text


def test_get_register_avatar_requires_login(client: TestClient) -> None:
    response = client.get("/register/avatar", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"

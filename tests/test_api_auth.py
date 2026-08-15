"""End-to-end registration/login/logout through the real ASGI app.

Each test gets its own on-disk SQLite file (`tmp_path`) and resets the process-wide
connection singleton (`app.db.connection._connection`) so accounts created by one test
never leak into another - `app.main`'s lifespan (which runs migrations) only fires when
`TestClient` is used as a context manager, so every test does that explicitly.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.db.connection as db_connection
from app.config import get_settings


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))
    get_settings.cache_clear()
    db_connection._connection = None

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()
    db_connection._connection = None


def _register(
    client: TestClient, email: str, password: str = "hunter2pass", nickname: str = ""
) -> object:
    return client.post(
        "/register",
        data={
            "email": email,
            "password": password,
            "password_confirm": password,
            "nickname": nickname,
        },
    )


def test_register_creates_a_session_and_shows_the_avatar_prompt(client: TestClient) -> None:
    # PR 106: registering no longer redirects straight home - it lands on an intermediate
    # "add an avatar?" screen first. The session cookie is set regardless (current_user's
    # email shows up in the sidebar via app/templating.py's context processor).
    response = _register(client, "alice@example.com")

    assert response.status_code == 200
    assert not response.history
    assert "Хотите добавить аватар?" in response.text
    assert 'action="/register/avatar"' in response.text
    assert "alice@example.com" in response.text


def test_register_nickname_is_optional(client: TestClient) -> None:
    # PR 105: the field mirrors PR 90's settings_account.html one, reusing the same
    # users.nickname column - registering without it must keep working exactly as before.
    response = _register(client, "alice@example.com")

    assert response.status_code == 200


def test_register_nickname_is_stored_on_the_account(client: TestClient) -> None:
    _register(client, "alice@example.com", nickname="Alice Wong")

    response = client.get("/settings/account")

    assert response.status_code == 200
    assert 'value="Alice Wong"' in response.text


def test_register_form_preserves_nickname_on_error(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = _register(client, "alice@example.com", nickname="Alice Wong")

    assert response.status_code == 400
    assert 'value="Alice Wong"' in response.text


def test_register_avatar_prompt_offers_a_skip_link_to_home(client: TestClient) -> None:
    response = _register(client, "alice@example.com")

    assert response.status_code == 200
    assert '<a href="/">Пропустить</a>' in response.text


_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_register_avatar_upload_saves_the_file_and_redirects_home(
    client: TestClient, tmp_path: Path
) -> None:
    _register(client, "alice@example.com")

    response = client.post(
        "/register/avatar", files={"avatar": ("me.png", _PNG_BYTES, "image/png")}
    )

    assert response.status_code == 200  # followed the redirect
    assert response.history[0].status_code == 303
    assert response.history[0].headers["location"] == "/"


def test_register_avatar_upload_rejects_a_disallowed_content_type(
    client: TestClient, tmp_path: Path
) -> None:
    _register(client, "alice@example.com")

    response = client.post(
        "/register/avatar",
        files={"avatar": ("me.gif", b"fake-gif-bytes", "image/gif")},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Хотите добавить аватар?" in response.text
    assert "Поддерживаются только изображения" in response.text


def test_register_avatar_upload_requires_login(client: TestClient) -> None:
    response = client.post(
        "/register/avatar",
        files={"avatar": ("me.png", _PNG_BYTES, "image/png")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_register_duplicate_email_shows_form_error(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = _register(client, "alice@example.com")

    assert response.status_code == 400
    assert "уже зарегистрирован" in response.text


def test_register_password_mismatch_shows_form_error(client: TestClient) -> None:
    response = client.post(
        "/register",
        data={
            "email": "alice@example.com",
            "password": "hunter2pass",
            "password_confirm": "different",
        },
    )

    assert response.status_code == 400
    assert "не совпадают" in response.text


def test_login_wrong_password_shows_form_error(client: TestClient) -> None:
    _register(client, "alice@example.com", password="hunter2pass")
    client.post("/logout")

    response = client.post(
        "/login", data={"email": "alice@example.com", "password": "wrong-password"}
    )

    assert response.status_code == 400
    assert "Неверный email или пароль" in response.text


def test_login_unknown_email_shows_same_form_error(client: TestClient) -> None:
    response = client.post(
        "/login", data={"email": "nobody@example.com", "password": "whatever"}
    )

    assert response.status_code == 400
    assert "Неверный email или пароль" in response.text


# --- PR 128: email placeholders --------------------------------------------------------


def test_login_email_field_has_a_placeholder(client: TestClient) -> None:
    response = client.get("/login")

    assert response.status_code == 200
    assert 'placeholder="you@example.com"' in response.text


def test_register_email_field_has_a_placeholder(client: TestClient) -> None:
    response = client.get("/register")

    assert response.status_code == 200
    assert 'placeholder="you@example.com"' in response.text


def test_login_placeholder_is_not_submitted_as_the_email_value(client: TestClient) -> None:
    """A placeholder is never part of a form's submitted data - browsers only send
    `value`. Confirms a re-rendered error form echoes what was actually typed, not the
    placeholder text sneaking into `submitted_email`, and the placeholder itself is still
    intact for the (still-empty) password field's neighbor."""
    response = client.post(
        "/login", data={"email": "alice@example.com", "password": "wrong-password"}
    )

    assert response.status_code == 400
    assert 'value="alice@example.com"' in response.text
    assert 'value="you@example.com"' not in response.text
    assert 'placeholder="you@example.com"' in response.text


def test_register_placeholder_is_not_submitted_as_the_email_value(client: TestClient) -> None:
    response = client.post(
        "/register",
        data={
            "email": "alice@example.com",
            "password": "hunter2pass",
            "password_confirm": "different",
        },
    )

    assert response.status_code == 400
    assert 'value="alice@example.com"' in response.text
    assert 'value="you@example.com"' not in response.text
    assert 'placeholder="you@example.com"' in response.text


def test_login_correct_credentials_establishes_session(client: TestClient) -> None:
    _register(client, "alice@example.com", password="hunter2pass")
    client.post("/logout")

    response = client.post(
        "/login", data={"email": "alice@example.com", "password": "hunter2pass"}
    )

    assert response.status_code == 200
    assert response.history[0].status_code == 303
    assert "alice@example.com" in response.text


def test_login_without_remember_me_uses_the_default_session_lifetime(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com", password="hunter2pass")
    client.post("/logout")

    response = client.post(
        "/login",
        data={"email": "alice@example.com", "password": "hunter2pass"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert f"Max-Age={get_settings().session_max_age}" in response.headers["set-cookie"]


def test_login_with_remember_me_extends_the_session_lifetime(client: TestClient) -> None:
    _register(client, "alice@example.com", password="hunter2pass")
    client.post("/logout")

    response = client.post(
        "/login",
        data={
            "email": "alice@example.com",
            "password": "hunter2pass",
            "remember_me": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (
        f"Max-Age={get_settings().session_remember_max_age}" in response.headers["set-cookie"]
    )


def test_logout_clears_session(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.post("/logout")
    home = client.get("/")

    assert response.status_code in (200, 303)
    assert "alice@example.com" not in home.text
    assert 'href="/login"' in home.text

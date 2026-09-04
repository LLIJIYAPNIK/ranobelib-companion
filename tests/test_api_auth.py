"""End-to-end registration/login/logout through the real ASGI app.

Each test wipes and re-migrates the shared test Postgres database (see
tests/db_reset.py) so accounts created by one test never leak into another -
`app.main`'s lifespan (which runs migrations) only fires when `TestClient` is used as a
context manager, so every test does that explicitly.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from tests.db_reset import reset_app_database


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


def test_register_avatar_prompt_is_a_clickable_preview_that_auto_submits(
    client: TestClient,
) -> None:
    # PR 174: revisits PR 106's original plain file field/"Загрузить" button - now the
    # same round avatar-preview widget PR 109 already gave /settings/account, reusing its
    # avatar-upload.js rather than a second implementation of the same thing. "Пропустить"
    # itself is untouched (see the test above).
    response = _register(client, "alice@example.com")

    assert response.status_code == 200
    assert 'data-role="avatar-upload-form"' in response.text
    assert 'class="avatar-upload"' in response.text
    assert 'data-role="avatar-upload-preview"' in response.text
    assert 'class="avatar-upload__initials"' in response.text
    assert 'data-role="avatar-upload-input"' in response.text
    assert "static/js/avatar-upload.js" in response.text
    assert ">Загрузить<" not in response.text


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


def test_register_duplicate_nickname_shows_form_error(client: TestClient) -> None:
    _register(client, "alice@example.com", nickname="Nick")

    response = _register(client, "bob@example.com", nickname="Nick")

    assert response.status_code == 400
    assert "никнейм уже занят" in response.text


def test_register_duplicate_nickname_is_case_insensitive(client: TestClient) -> None:
    _register(client, "alice@example.com", nickname="Nick")

    response = _register(client, "bob@example.com", nickname="nick")

    assert response.status_code == 400
    assert "никнейм уже занят" in response.text


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


# --- PR 184: minimum password strength on registration ---------------------------------


def test_register_weak_password_shows_form_error(client: TestClient) -> None:
    response = client.post(
        "/register",
        data={
            "email": "alice@example.com",
            "password": "short1",
            "password_confirm": "short1",
        },
    )

    assert response.status_code == 400
    assert "слишком просто" in response.text


def test_register_blocklisted_password_shows_form_error(client: TestClient) -> None:
    response = client.post(
        "/register",
        data={
            "email": "alice@example.com",
            "password": "password",
            "password_confirm": "password",
        },
    )

    assert response.status_code == 400
    assert "слишком просто" in response.text


def test_register_eight_char_password_is_accepted(client: TestClient) -> None:
    response = _register(client, "alice@example.com", password="eightchr")

    assert response.status_code == 200
    assert not response.history


def test_register_seven_char_password_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/register",
        data={
            "email": "alice@example.com",
            "password": "sevench",
            "password_confirm": "sevench",
        },
    )

    assert response.status_code == 400
    assert "слишком просто" in response.text


def test_login_wrong_password_shows_form_error(client: TestClient) -> None:
    _register(client, "alice@example.com", password="hunter2pass")
    client.post("/logout")

    response = client.post(
        "/login", data={"email": "alice@example.com", "password": "wrong-password"}
    )

    assert response.status_code == 400
    assert "Неверный email или пароль" in response.text


def test_login_unknown_email_shows_same_form_error(client: TestClient) -> None:
    response = client.post("/login", data={"email": "nobody@example.com", "password": "whatever"})

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

    response = client.post("/login", data={"email": "alice@example.com", "password": "hunter2pass"})

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
    assert f"Max-Age={get_settings().session_remember_max_age}" in response.headers["set-cookie"]


# --- PR 188: rate limiting on /login and /register --------------------------------------


def test_login_is_rate_limited_after_repeated_wrong_passwords(client: TestClient) -> None:
    _register(client, "alice@example.com", password="hunter2pass")
    client.post("/logout")

    for _ in range(5):
        response = client.post(
            "/login", data={"email": "alice@example.com", "password": "wrong-password"}
        )
        assert response.status_code == 400

    response = client.post(
        "/login", data={"email": "alice@example.com", "password": "wrong-password"}
    )

    assert response.status_code == 429
    assert "Слишком много попыток" in response.text


def test_login_succeeds_after_a_few_failed_attempts_while_under_the_limit(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com", password="hunter2pass")
    client.post("/logout")

    for _ in range(2):
        client.post("/login", data={"email": "alice@example.com", "password": "wrong-password"})

    response = client.post("/login", data={"email": "alice@example.com", "password": "hunter2pass"})

    assert response.status_code == 200
    assert "alice@example.com" in response.text


def test_login_rate_limit_is_scoped_per_email(client: TestClient) -> None:
    for _ in range(6):
        client.post("/login", data={"email": "alice@example.com", "password": "wrong-password"})

    response = client.post(
        "/login", data={"email": "bob@example.com", "password": "wrong-password"}
    )

    assert response.status_code == 400  # not 429 - a different email isn't limited yet


def test_register_is_rate_limited_after_repeated_attempts(client: TestClient) -> None:
    for _ in range(5):
        _register(client, "alice@example.com")  # 1st succeeds, rest fail (duplicate email)

    response = _register(client, "alice@example.com")

    assert response.status_code == 429
    assert "Слишком много попыток" in response.text


def test_logout_clears_session(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.post("/logout")
    home = client.get("/")

    assert response.status_code in (200, 303)
    assert "alice@example.com" not in home.text
    assert 'href="/login"' in home.text

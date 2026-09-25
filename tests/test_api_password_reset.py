"""End-to-end "Забыли пароль?" flow (PR 225) through the real ASGI app.

app.email.send_email() is monkeypatched to a capturing stub rather than actually hitting
SMTP - EMAIL_HOST is unset in tests (see tests/test_api_auth.py's own client fixture,
which this mirrors), so app/email.py would just log a warning and return on its own, but
these tests need the emailed reset link's token, which otherwise never surfaces anywhere
a test could read it back (the raw token only ever exists in memory long enough to email -
see app/db/password_reset.py's own docstring on why).
"""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api.auth as auth_module
from app.config import get_settings
from tests.db_reset import reset_app_database


@pytest.fixture
def sent_emails(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    captured: list[dict[str, str]] = []

    async def _fake_send_email(to: str, subject: str, body: str) -> None:
        captured.append({"to": to, "subject": subject, "body": body})

    monkeypatch.setattr(auth_module, "send_email", _fake_send_email)
    return captured


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


def _register(client: TestClient, email: str, password: str = "hunter2pass") -> None:
    client.post(
        "/register",
        data={"email": email, "password": password, "password_confirm": password, "nickname": ""},
    )


def _extract_reset_token(body: str) -> str:
    match = re.search(r"/password-reset/([\w-]+)", body)
    assert match is not None, body
    return match.group(1)


# --- POST /password-reset - always the same neutral response ---------------------------


def test_request_reset_for_unknown_email_shows_neutral_message(
    client: TestClient, sent_emails: list[dict[str, str]]
) -> None:
    response = client.post("/password-reset", data={"email": "nobody@example.com"})

    assert response.status_code == 200
    assert "Если такой email зарегистрирован" in response.text
    assert sent_emails == []


def test_request_reset_for_registered_email_sends_a_link_and_shows_the_same_message(
    client: TestClient, sent_emails: list[dict[str, str]]
) -> None:
    _register(client, "alice@example.com")
    client.post("/logout")

    response = client.post("/password-reset", data={"email": "alice@example.com"})

    assert response.status_code == 200
    assert "Если такой email зарегистрирован" in response.text
    assert len(sent_emails) == 1
    assert sent_emails[0]["to"] == "alice@example.com"
    assert "/password-reset/" in sent_emails[0]["body"]


# --- GET/POST /password-reset/{token} ---------------------------------------------------


def test_reset_confirm_rejects_an_unknown_token(client: TestClient) -> None:
    response = client.get("/password-reset/not-a-real-token")

    assert response.status_code == 400
    assert "недействительна" in response.text


def test_reset_confirm_sets_a_new_password_and_it_can_be_used_to_log_in(
    client: TestClient, sent_emails: list[dict[str, str]]
) -> None:
    _register(client, "alice@example.com", password="original-pass")
    client.post("/logout")
    client.post("/password-reset", data={"email": "alice@example.com"})
    token = _extract_reset_token(sent_emails[0]["body"])

    response = client.post(
        f"/password-reset/{token}",
        data={"new_password": "brand-new-pass", "new_password_confirm": "brand-new-pass"},
    )
    assert response.status_code == 200
    assert "Пароль изменён" in response.text

    old_password_login = client.post(
        "/login", data={"email": "alice@example.com", "password": "original-pass"}
    )
    assert old_password_login.status_code == 400

    new_password_login = client.post(
        "/login", data={"email": "alice@example.com", "password": "brand-new-pass"}
    )
    assert new_password_login.status_code == 200


def test_reset_confirm_rejects_a_reused_token(
    client: TestClient, sent_emails: list[dict[str, str]]
) -> None:
    _register(client, "alice@example.com", password="original-pass")
    client.post("/logout")
    client.post("/password-reset", data={"email": "alice@example.com"})
    token = _extract_reset_token(sent_emails[0]["body"])
    client.post(
        f"/password-reset/{token}",
        data={"new_password": "brand-new-pass", "new_password_confirm": "brand-new-pass"},
    )

    response = client.post(
        f"/password-reset/{token}",
        data={"new_password": "another-pass1", "new_password_confirm": "another-pass1"},
    )

    assert response.status_code == 400
    assert "недействительна" in response.text


def test_reset_confirm_rejects_mismatched_passwords(
    client: TestClient, sent_emails: list[dict[str, str]]
) -> None:
    _register(client, "alice@example.com")
    client.post("/logout")
    client.post("/password-reset", data={"email": "alice@example.com"})
    token = _extract_reset_token(sent_emails[0]["body"])

    response = client.post(
        f"/password-reset/{token}",
        data={"new_password": "brand-new-pass", "new_password_confirm": "different-pass"},
    )

    assert response.status_code == 400
    assert "не совпадают" in response.text


def test_reset_confirm_rejects_a_weak_password(
    client: TestClient, sent_emails: list[dict[str, str]]
) -> None:
    _register(client, "alice@example.com")
    client.post("/logout")
    client.post("/password-reset", data={"email": "alice@example.com"})
    token = _extract_reset_token(sent_emails[0]["body"])

    response = client.post(
        f"/password-reset/{token}",
        data={"new_password": "short1", "new_password_confirm": "short1"},
    )

    assert response.status_code == 400
    assert "слишком просто" in response.text


# --- PR 225: resetting invalidates sessions issued before it ----------------------------


def test_reset_invalidates_a_session_that_was_already_logged_in(
    client: TestClient, sent_emails: list[dict[str, str]]
) -> None:
    _register(client, "alice@example.com", password="original-pass")
    old_session_cookie = client.cookies.get("session")
    assert old_session_cookie is not None

    client.post("/password-reset", data={"email": "alice@example.com"})
    token = _extract_reset_token(sent_emails[0]["body"])
    client.post(
        f"/password-reset/{token}",
        data={"new_password": "brand-new-pass", "new_password_confirm": "brand-new-pass"},
    )

    # Simulate the old, still-logged-in browser tab that never saw the reset happen -
    # its cookie predates the reset and must no longer be treated as logged in.
    client.cookies.set("session", old_session_cookie)
    response = client.get("/settings/account")

    assert 'href="/login"' in response.text or response.status_code == 303

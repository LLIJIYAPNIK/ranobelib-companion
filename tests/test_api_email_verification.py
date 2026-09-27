"""PR 246: email confirmation at registration, end to end through the real app.

A new account can't log in until it confirms its email with the link or the 6-digit code
from the confirmation email. Outgoing email is captured by conftest.py's `_capture_emails`.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth.rate_limit import _attempts
from app.config import get_settings
from app.db.email_verification import MAX_FAILED_CODE_ATTEMPTS
from tests.auth_helpers import (
    latest_verification_code,
    latest_verification_path,
    register,
    sent_emails,
)
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


def _submit_registration(client: TestClient, email: str = "alice@example.com") -> object:
    return client.post(
        "/register",
        data={"email": email, "password": "hunter2pass", "password_confirm": "hunter2pass"},
    )


def _is_logged_in(client: TestClient) -> bool:
    return client.get("/register/avatar", follow_redirects=False).status_code == 200


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


# --- Registering ------------------------------------------------------------------------


def test_registering_does_not_log_in(client: TestClient) -> None:
    response = _submit_registration(client)

    assert response.status_code == 200
    assert response.history[0].headers["location"] == "/verify-email"
    assert "Мы отправили письмо на" in response.text
    assert "alice@example.com" in response.text
    assert not _is_logged_in(client)


def test_registering_sends_one_email_with_a_link_and_a_code(client: TestClient) -> None:
    _submit_registration(client)

    assert len(sent_emails) == 1
    assert sent_emails[0].to == "alice@example.com"
    assert latest_verification_path("alice@example.com").startswith("/verify-email/")
    assert len(latest_verification_code("alice@example.com")) == 6


def test_verify_email_page_without_a_pending_account_goes_to_login(client: TestClient) -> None:
    response = client.get("/verify-email", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# --- Confirming with the code -----------------------------------------------------------


def test_correct_code_logs_in_and_continues_to_the_avatar_step(client: TestClient) -> None:
    _submit_registration(client)

    response = client.post(
        "/verify-email",
        data={"code": latest_verification_code("alice@example.com")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/register/avatar"
    assert _is_logged_in(client)


def test_wrong_code_shows_an_error_and_stays_logged_out(client: TestClient) -> None:
    _submit_registration(client)

    response = client.post(
        "/verify-email", data={"code": _wrong(latest_verification_code("alice@example.com"))}
    )

    assert response.status_code == 400
    assert "Неверный или устаревший код" in response.text
    assert not _is_logged_in(client)


def test_code_stops_working_after_too_many_wrong_attempts(client: TestClient) -> None:
    _submit_registration(client)
    code = latest_verification_code("alice@example.com")
    for _ in range(MAX_FAILED_CODE_ATTEMPTS):
        client.post("/verify-email", data={"code": _wrong(code)})
    # Isolates the per-email cap from the per-IP limiter, which also allows 5 a minute.
    _attempts.clear()

    response = client.post("/verify-email", data={"code": code})

    # Same message as any other bad code - the page offers a new email either way.
    assert response.status_code == 400
    assert "Неверный или устаревший код" in response.text
    assert not _is_logged_in(client)


def test_code_attempts_are_rate_limited_per_ip(client: TestClient) -> None:
    # The per-IP limiter is the second layer on top of the per-email cap above.
    _submit_registration(client, "alice@example.com")
    for _ in range(5):
        client.post("/verify-email", data={"code": "000000"})

    response = client.post("/verify-email", data={"code": "000000"})

    assert response.status_code == 429


def test_code_submit_without_a_pending_account_goes_to_login(client: TestClient) -> None:
    response = client.post("/verify-email", data={"code": "123456"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# --- Confirming with the link -----------------------------------------------------------


def test_link_logs_in_and_continues_to_the_avatar_step(client: TestClient) -> None:
    _submit_registration(client)

    response = client.get(latest_verification_path("alice@example.com"), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/register/avatar"
    assert _is_logged_in(client)


def test_link_works_in_a_different_browser(client: TestClient) -> None:
    # The whole point of the link: the email is often opened somewhere else.
    _submit_registration(client)
    path = latest_verification_path("alice@example.com")
    client.cookies.clear()

    response = client.get(path, follow_redirects=False)

    assert response.headers["location"] == "/register/avatar"
    assert _is_logged_in(client)


def test_used_link_is_rejected(client: TestClient) -> None:
    _submit_registration(client)
    path = latest_verification_path("alice@example.com")
    client.get(path)
    client.post("/logout")

    response = client.get(path)

    assert response.status_code == 400
    assert "Ссылка недействительна" in response.text
    assert not _is_logged_in(client)


def test_unknown_link_is_rejected_with_the_same_message(client: TestClient) -> None:
    response = client.get("/verify-email/not-a-real-token")

    assert response.status_code == 400
    assert "Ссылка недействительна" in response.text


def test_using_the_link_retires_the_code(client: TestClient) -> None:
    _submit_registration(client)
    code = latest_verification_code("alice@example.com")
    client.get(latest_verification_path("alice@example.com"))
    client.post("/logout")
    client.post("/login", data={"email": "alice@example.com", "password": "hunter2pass"})

    # Now verified, so logging in went straight through - there's nothing pending for
    # the old code to confirm.
    response = client.post("/verify-email", data={"code": code}, follow_redirects=False)
    assert response.headers["location"] == "/login"


# --- Resending --------------------------------------------------------------------------


def test_resend_sends_a_new_email_and_retires_the_old_one(client: TestClient) -> None:
    _submit_registration(client)
    old_code = latest_verification_code("alice@example.com")
    old_path = latest_verification_path("alice@example.com")

    response = client.post("/verify-email/resend")

    assert response.status_code == 200
    assert "Письмо отправлено ещё раз" in response.text
    assert len(sent_emails) == 2
    assert client.get(old_path, follow_redirects=False).status_code == 400
    if latest_verification_code("alice@example.com") != old_code:
        bad = client.post("/verify-email", data={"code": old_code})
        assert bad.status_code == 400
    good = client.post(
        "/verify-email",
        data={"code": latest_verification_code("alice@example.com")},
        follow_redirects=False,
    )
    assert good.headers["location"] == "/register/avatar"


def test_resend_shares_the_register_rate_limit(client: TestClient) -> None:
    # PR 188's register limiter, same bucket: registering was attempt 1.
    _submit_registration(client)
    for _ in range(4):
        assert client.post("/verify-email/resend").status_code == 200

    response = client.post("/verify-email/resend")

    assert response.status_code == 429
    assert len(sent_emails) == 5


def test_resend_without_a_pending_account_goes_to_login(client: TestClient) -> None:
    response = client.post("/verify-email/resend", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert sent_emails == []


# --- Logging in -------------------------------------------------------------------------


def test_login_with_an_unconfirmed_email_goes_to_confirmation(client: TestClient) -> None:
    _submit_registration(client)
    client.cookies.clear()

    response = client.post(
        "/login",
        data={"email": "alice@example.com", "password": "hunter2pass"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/verify-email"
    assert not _is_logged_in(client)
    # No new email on login - the page offers a resend instead.
    assert len(sent_emails) == 1


def test_login_with_an_unconfirmed_email_can_still_confirm_by_code(client: TestClient) -> None:
    _submit_registration(client)
    client.cookies.clear()
    client.post("/login", data={"email": "alice@example.com", "password": "hunter2pass"})

    client.post("/verify-email", data={"code": latest_verification_code("alice@example.com")})

    assert _is_logged_in(client)


def test_login_wrong_password_for_unconfirmed_account_gives_the_usual_error(
    client: TestClient,
) -> None:
    # Only the right password reveals the account is waiting for confirmation.
    _submit_registration(client)
    client.cookies.clear()

    response = client.post("/login", data={"email": "alice@example.com", "password": "nope"})

    assert response.status_code == 400
    assert "Неверный email или пароль" in response.text


def test_login_with_a_confirmed_email_logs_in(client: TestClient) -> None:
    register(client, "alice@example.com")
    client.post("/logout")

    response = client.post(
        "/login",
        data={"email": "alice@example.com", "password": "hunter2pass"},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/"
    assert _is_logged_in(client)

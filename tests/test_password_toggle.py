"""Show/hide toggle on every password field (PR 227).

Checks the rendered markup, not the click behavior itself (that's password-toggle.js in a
real browser): every <input type="password"> on each form sits in the
.auth-form__password-field wrapper next to a [data-role="toggle-password"] button that
points back at it, and the script is loaded from base.html so the auth modal's injected
card (whose own <script> tags never run) is covered too.
"""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api.auth as auth_module
from app.config import get_settings
from tests.db_reset import reset_app_database

_TOGGLE_SCRIPT = "js/password-toggle.js"
_PASSWORD_INPUT = re.compile(r'<input[^>]*type="password"[^>]*>')


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


def _assert_every_password_field_has_a_toggle(html: str, expected_ids: list[str]) -> None:
    inputs = _PASSWORD_INPUT.findall(html)
    ids = [re.search(r'id="([^"]+)"', tag).group(1) for tag in inputs]  # type: ignore[union-attr]
    assert ids == expected_ids
    for input_id in ids:
        wrapped = re.search(
            rf'<div class="auth-form__password-field">\s*<input[^>]*id="{input_id}"[^>]*>\s*'
            rf'<button[^>]*data-role="toggle-password"[^>]*aria-controls="{input_id}"',
            html,
        )
        assert wrapped is not None, input_id


def test_login_page_password_field_has_a_toggle(client: TestClient) -> None:
    response = client.get("/login")

    _assert_every_password_field_has_a_toggle(response.text, ["login-password"])
    assert _TOGGLE_SCRIPT in response.text


def test_register_page_password_fields_have_toggles(client: TestClient) -> None:
    response = client.get("/register")

    _assert_every_password_field_has_a_toggle(
        response.text, ["register-password", "register-password-confirm"]
    )
    assert _TOGGLE_SCRIPT in response.text


def test_auth_modal_fragment_password_fields_have_toggles(client: TestClient) -> None:
    response = client.get("/register", headers={"X-Requested-With": "XMLHttpRequest"})

    _assert_every_password_field_has_a_toggle(
        response.text, ["register-password", "register-password-confirm"]
    )


def test_page_behind_the_auth_modal_loads_the_toggle_script(client: TestClient) -> None:
    # The modal's fragment can't bring its own copy (innerHTML scripts never run), so
    # whatever page the modal opens over has to have it already.
    response = client.get("/")

    assert _TOGGLE_SCRIPT in response.text


def test_settings_security_password_fields_have_toggles(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/settings/security")

    _assert_every_password_field_has_a_toggle(
        response.text,
        ["security-current-password", "security-new-password", "security-new-password-confirm"],
    )
    assert _TOGGLE_SCRIPT in response.text


def test_password_reset_form_fields_have_toggles(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[str] = []

    async def _fake_send_email(to: str, subject: str, body: str) -> None:
        sent.append(body)

    monkeypatch.setattr(auth_module, "send_email", _fake_send_email)
    _register(client, "alice@example.com")
    client.post("/logout")
    client.post("/password-reset", data={"email": "alice@example.com"})
    token = re.search(r"/password-reset/([\w-]+)", sent[0]).group(1)  # type: ignore[union-attr]

    response = client.get(f"/password-reset/{token}")

    _assert_every_password_field_has_a_toggle(
        response.text, ["password-reset-new-password", "password-reset-new-password-confirm"]
    )
    # The macro keeps the autofocus the reset form's first field had before PR 227.
    assert re.search(r'id="password-reset-new-password"[^>]*autofocus', response.text)

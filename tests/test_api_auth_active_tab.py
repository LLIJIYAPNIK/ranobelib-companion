"""PR 219: _auth_tabs.html has always branched on `active_tab` to highlight the current
tab, but no route in app/api/auth.py ever passed it - the "Вход"/"Регистрация" tab was
never highlighted on either screen. Covers the GET routes and a representative POST
error branch for each mode; the fix (adding `active_tab` alongside the pre-existing
`mode` key) is applied uniformly across all seven render call sites in app/api/auth.py.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from tests.db_reset import reset_app_database

_ACTIVE_CLASS = "auth-tabs__link--active"


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


def _active_tab_label(html: str) -> str:
    login_active = f'{_ACTIVE_CLASS}" href="/login"' in html
    register_active = f'{_ACTIVE_CLASS}" href="/register"' in html
    assert login_active != register_active, "expected exactly one tab to be active"
    return "login" if login_active else "register"


def test_get_login_highlights_login_tab(client: TestClient) -> None:
    response = client.get("/login")

    assert response.status_code == 200
    assert _active_tab_label(response.text) == "login"


def test_get_register_highlights_register_tab(client: TestClient) -> None:
    response = client.get("/register")

    assert response.status_code == 200
    assert _active_tab_label(response.text) == "register"


def test_login_error_still_highlights_login_tab(client: TestClient) -> None:
    response = client.post(
        "/login", data={"email": "nobody@example.com", "password": "wrong"}
    )

    assert response.status_code == 400
    assert _active_tab_label(response.text) == "login"


def test_register_error_still_highlights_register_tab(client: TestClient) -> None:
    client.post(
        "/register",
        data={
            "email": "alice@example.com",
            "password": "hunter2pass",
            "password_confirm": "hunter2pass",
        },
    )

    response = client.post(
        "/register",
        data={
            "email": "alice@example.com",
            "password": "hunter2pass",
            "password_confirm": "hunter2pass",
        },
    )

    assert response.status_code == 400
    assert _active_tab_label(response.text) == "register"

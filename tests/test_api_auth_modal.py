"""PR 218: the sidebar's guest link opens login/register as a modal over the current
page (auth-modal.js) instead of navigating to /login - see app/templates/base.html and
tests/test_api_auth_fragment.py for the fragment rendering the modal loads.
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


def _register(client: TestClient, email: str) -> None:
    client.post(
        "/register",
        data={"email": email, "password": "hunter2pass", "password_confirm": "hunter2pass"},
    )


def test_sidebar_guest_link_is_marked_as_the_modal_trigger(client: TestClient) -> None:
    response = client.get("/")

    assert 'data-role="auth-modal-trigger"' in response.text
    assert "static/js/auth-modal.js" in response.text


def test_auth_modal_script_omitted_when_logged_in(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/")

    assert "static/js/auth-modal.js" not in response.text

"""The global "file ready" toast container/script in base.html (PR 50) - gated to logged
in visitors, since GET /downloads/ready (what it polls) requires an account."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from tests.db_reset import reset_app_database

client = TestClient(app)


@pytest.fixture
def logged_in_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

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


def test_anonymous_visitor_gets_no_download_ready_banner() -> None:
    response = client.get("/")

    assert 'data-role="download-ready"' not in response.text
    assert "static/js/download-ready.js" not in response.text


def test_logged_in_visitor_gets_the_download_ready_banner(
    logged_in_client: TestClient,
) -> None:
    response = logged_in_client.get("/")

    assert 'data-role="download-ready"' in response.text
    assert "static/js/download-ready.js" in response.text

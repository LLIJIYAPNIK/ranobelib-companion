"""The sidebar "Загрузки" badge (PR 56) - a global element in base.html, gated to logged
in visitors like the download-ready banner (PR 50), driven by the same downloads-status.js
poll the "Загрузки"/"Активность" sections already use (PR 17/18), not a second one."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.db.connection as db_connection
import app.jobs.store as job_store
from app.config import get_settings
from app.jobs.store import create_job
from app.main import app

client = TestClient(app)


@pytest.fixture
def logged_in_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    get_settings.cache_clear()
    db_connection._connection = None
    monkeypatch.setattr(job_store, "_jobs", {})

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
    db_connection._connection = None


def test_anonymous_visitor_gets_no_downloads_badge() -> None:
    response = client.get("/")

    assert 'data-role="downloads-badge"' not in response.text
    assert "static/js/downloads-status.js" not in response.text


def test_logged_in_visitor_gets_the_downloads_badge_on_any_page(
    logged_in_client: TestClient,
) -> None:
    response = logged_in_client.get("/")

    assert 'data-role="downloads-badge"' in response.text
    assert "static/js/downloads-status.js" in response.text


def test_downloads_badge_starts_hidden_with_no_active_jobs(
    logged_in_client: TestClient,
) -> None:
    response = logged_in_client.get("/")

    assert '<span class="sidebar__badge" data-role="downloads-badge" hidden>0</span>' in (
        response.text
    )


def test_downloads_page_loads_the_status_script_only_once(
    logged_in_client: TestClient,
) -> None:
    """downloads-status.js used to be injected by the active-downloads macro itself -
    now that base.html always loads it for logged in visitors, the macro must not also
    add it, or a job list page would end up polling twice."""
    job = create_job("6712--test-novel", "epub", user_id=1)
    job.status = "running"
    job.completed = 3
    job.total = 10

    response = logged_in_client.get("/downloads")

    assert response.text.count("static/js/downloads-status.js") == 1

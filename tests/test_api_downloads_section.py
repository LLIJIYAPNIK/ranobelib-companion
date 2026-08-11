"""The "Загрузки" section: GET /downloads (page) and GET /downloads/status (JSON list
app/static/js/downloads-status.js polls)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.db.connection as db_connection
import app.jobs.store as job_store
from app.config import get_settings
from app.db.connection import get_connection
from app.db.downloads import list_download_history, record_download
from app.jobs.store import create_job, delete_result_file


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    get_settings.cache_clear()
    db_connection._connection = None
    # _jobs is a process-wide dict (see app/jobs/store.py) - reset it too, otherwise a
    # job from an earlier test (whose isolated DB also happened to hand out user_id=1)
    # would leak into this one.
    monkeypatch.setattr(job_store, "_jobs", {})

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()
    db_connection._connection = None


def _register(client: TestClient, email: str = "alice@example.com") -> None:
    client.post(
        "/register",
        data={"email": email, "password": "hunter2pass", "password_confirm": "hunter2pass"},
    )


def test_list_downloads_status_requires_login(client: TestClient) -> None:
    response = client.get("/downloads/status", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_list_downloads_status_empty_for_new_user(client: TestClient) -> None:
    _register(client)

    response = client.get("/downloads/status")

    assert response.status_code == 200
    assert response.json() == []


def test_list_downloads_status_shows_this_users_active_job(client: TestClient) -> None:
    _register(client)  # user id 1
    job = create_job("6712--test-novel", "epub", user_id=1)
    job.status = "running"
    job.completed = 3
    job.total = 10

    response = client.get("/downloads/status")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["job_id"] == job.id
    assert body[0]["slug_url"] == "6712--test-novel"
    assert body[0]["fmt"] == "epub"
    assert body[0]["status"] == "running"
    assert body[0]["completed"] == 3
    assert body[0]["total"] == 10
    assert "eta_seconds" in body[0]


def test_list_downloads_status_omits_other_users_jobs(client: TestClient) -> None:
    _register(client)  # user id 1
    create_job("6712--other-novel", "epub", user_id=999)

    response = client.get("/downloads/status")

    assert response.json() == []


def test_list_downloads_status_omits_terminal_jobs(client: TestClient) -> None:
    _register(client)  # user id 1
    done_job = create_job("6712--done-novel", "epub", user_id=1)
    done_job.status = "done"
    error_job = create_job("6712--error-novel", "epub", user_id=1)
    error_job.status = "error"
    active_job = create_job("6712--active-novel", "epub", user_id=1)
    active_job.status = "running"

    response = client.get("/downloads/status")

    body = response.json()
    assert [entry["job_id"] for entry in body] == [active_job.id]


def test_list_downloads_status_omits_anonymous_jobs(client: TestClient) -> None:
    _register(client)  # user id 1
    create_job("6712--anon-novel", "epub", user_id=None)

    response = client.get("/downloads/status")

    assert response.json() == []


def test_list_downloads_ready_requires_login(client: TestClient) -> None:
    response = client.get("/downloads/ready", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_list_downloads_ready_empty_for_new_user(client: TestClient) -> None:
    _register(client)

    response = client.get("/downloads/ready")

    assert response.status_code == 200
    assert response.json() == []


def test_list_downloads_ready_shows_finished_undelivered_job(client: TestClient) -> None:
    _register(client)  # user id 1
    job = create_job("6712--test-novel", "epub", user_id=1)
    job.status = "done"
    job.result_path = Path("/tmp/whatever.epub")

    response = client.get("/downloads/ready")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["job_id"] == job.id
    assert body[0]["slug_url"] == "6712--test-novel"
    assert body[0]["fmt"] == "epub"
    assert body[0]["file_url"] == f"/titles/6712--test-novel/download/{job.id}/file"


def test_list_downloads_ready_omits_active_and_delivered_jobs(client: TestClient) -> None:
    _register(client)  # user id 1
    active_job = create_job("6712--active-novel", "epub", user_id=1)
    active_job.status = "running"
    delivered_job = create_job("6712--delivered-novel", "epub", user_id=1)
    delivered_job.status = "done"
    delivered_job.result_path = None

    response = client.get("/downloads/ready")

    assert response.json() == []


def test_list_downloads_ready_omits_other_users_jobs(client: TestClient) -> None:
    _register(client)  # user id 1
    other_job = create_job("6712--other-novel", "epub", user_id=999)
    other_job.status = "done"
    other_job.result_path = Path("/tmp/whatever.epub")

    response = client.get("/downloads/ready")

    assert response.json() == []


def test_show_downloads_anonymous_is_viewable_but_prompts_to_log_in(
    client: TestClient,
) -> None:
    response = client.get("/downloads")

    assert response.status_code == 200
    assert 'href="/login"' in response.text
    assert 'href="/register"' in response.text


def test_show_downloads_empty_state(client: TestClient) -> None:
    _register(client)

    response = client.get("/downloads")

    assert response.status_code == 200
    assert "Сейчас ничего не скачивается" in response.text
    assert "Пока ничего не скачивали" in response.text


def test_show_downloads_lists_active_job_with_progress(client: TestClient) -> None:
    _register(client)  # user id 1
    job = create_job("6712--test-novel", "epub", user_id=1)
    job.status = "running"
    job.completed = 3
    job.total = 10

    response = client.get("/downloads")

    assert response.status_code == 200
    assert f'data-job-id="{job.id}"' in response.text
    assert "6712--test-novel" in response.text
    assert "Глава 3 из 10" in response.text
    assert "static/js/downloads-status.js" in response.text


def test_show_downloads_lists_history(client: TestClient) -> None:
    _register(client)  # user id 1
    record_download(get_connection(), 1, "6712--test-novel", "epub", "done", 42, None)

    response = client.get("/downloads")

    assert response.status_code == 200
    assert "6712--test-novel" in response.text
    assert "Готово" in response.text
    assert "42 глав" in response.text


def test_show_downloads_history_shows_error(client: TestClient) -> None:
    _register(client)  # user id 1
    record_download(get_connection(), 1, "6712--test-novel", "epub", "error", None, "Опа")

    response = client.get("/downloads")

    assert "Опа" in response.text


def test_show_downloads_offers_download_link_for_a_ready_job(client: TestClient) -> None:
    # PR 58: even if the visitor closed the "file ready" toast (PR 50) without clicking
    # through, the file is still on disk and this page should still offer it.
    _register(client)  # user id 1
    job = create_job("6712--test-novel", "epub", user_id=1)
    job.status = "done"
    job.result_path = Path("/tmp/whatever.epub")
    record_download(
        get_connection(), 1, "6712--test-novel", "epub", "done", 1, None, job_id=job.id
    )

    response = client.get("/downloads")

    assert response.status_code == 200
    assert f'href="/titles/6712--test-novel/download/{job.id}/file"' in response.text


def test_show_downloads_omits_download_link_once_the_file_is_gone(client: TestClient) -> None:
    _register(client)  # user id 1
    job = create_job("6712--test-novel", "epub", user_id=1)
    job.status = "done"
    job.result_path = Path("/tmp/whatever.epub")
    record_download(
        get_connection(), 1, "6712--test-novel", "epub", "done", 1, None, job_id=job.id
    )
    delete_result_file(job)

    response = client.get("/downloads")

    assert f"/download/{job.id}/file" not in response.text


def test_show_downloads_omits_download_link_for_a_row_with_no_job_id(
    client: TestClient,
) -> None:
    # A history row written before PR 58 (or an anonymous download - see record_download())
    # has no job_id to look up, so it must not render a broken/absent link.
    _register(client)  # user id 1
    record_download(get_connection(), 1, "6712--test-novel", "epub", "done", 1, None)

    response = client.get("/downloads")

    assert response.status_code == 200
    assert "downloads-history__download" not in response.text


def test_delete_history_entry_requires_login(client: TestClient) -> None:
    response = client.delete("/downloads/history/1", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_delete_history_entry_removes_own_entry(client: TestClient) -> None:
    _register(client)  # user id 1
    record_download(get_connection(), 1, "6712--test-novel", "epub", "done", 1, None)
    entry_id = list_download_history(get_connection(), 1)[0].id

    response = client.delete(f"/downloads/history/{entry_id}")

    assert response.status_code == 204
    assert list_download_history(get_connection(), 1) == []


def test_delete_history_entry_unknown_id_is_404(client: TestClient) -> None:
    _register(client)

    response = client.delete("/downloads/history/999")

    assert response.status_code == 404


def test_delete_history_entry_cannot_remove_another_users_entry(client: TestClient) -> None:
    _register(client, "alice@example.com")  # user id 1
    _register(client, "bob@example.com")  # user id 2, now the logged in session
    record_download(get_connection(), 1, "6712--test-novel", "epub", "done", 1, None)
    entry_id = list_download_history(get_connection(), 1)[0].id

    response = client.delete(f"/downloads/history/{entry_id}")

    assert response.status_code == 404
    assert len(list_download_history(get_connection(), 1)) == 1

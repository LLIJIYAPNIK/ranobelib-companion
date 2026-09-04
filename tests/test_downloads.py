import os
import tempfile
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib import MultipleTitleTranslationsError
from ranobelib.exceptions import AmbiguousChapter
from ranobelib.models import Chapter, ChapterBranch, ChapterUser, Team, Volume

import app.jobs.store as job_store
from app.config import get_settings
from app.db.connection import connection
from app.db.downloads import list_download_history
from app.jobs.store import create_job, get_job
from app.main import app
from tests.db_reset import reset_app_database

client = TestClient(app, follow_redirects=False)


class _FakeClient:
    def __init__(self, volumes: list[Volume]) -> None:
        self._volumes = volumes

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def download_title(
        self,
        *,
        branch_id: int | None = None,
        translation_index: int | None = None,
        chapter_delay: float = 0.0,
        on_chapter: object = None,
    ) -> list[Volume]:
        chapters = [chapter for volume in self._volumes for chapter in volume.chapters]
        total = len(chapters)
        for index in range(total):
            if on_chapter is not None:
                on_chapter(index + 1, total)
        return self._volumes

    async def export(self, chapters: list[Chapter], *, fmt: str, path: str) -> str:
        with open(path, "w", encoding="utf-8") as f:
            f.write("exported content")
        return path


class _AmbiguousClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def __aenter__(self) -> "_AmbiguousClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def download_title(self, **kwargs: object) -> list[Volume]:
        raise self._exc


def _branch(branch_id: int, team_name: str | None = None) -> ChapterBranch:
    teams = (
        [Team(id=branch_id, slug=f"t{branch_id}", slug_url=f"t{branch_id}", name=team_name)]
        if team_name
        else []
    )
    return ChapterBranch(
        id=branch_id,
        branch_id=branch_id,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        teams=teams,
        user=ChapterUser(id=branch_id, username=f"user{branch_id}"),
    )


def _job_id_from_location(location: str) -> str:
    return location.rsplit("/", 1)[-1]


def _wait_until_terminal(job_id: str, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = get_job(job_id)
        if job is not None and job.status in ("done", "error", "needs_translation"):
            return
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach a terminal state in time")


@pytest.fixture
def logged_in_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Isolated DB + an authenticated session - see tests/test_api_auth.py."""
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

    with TestClient(app, follow_redirects=False) as test_client:
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


def test_start_download_requires_login() -> None:
    response = client.post("/titles/6712--test-novel/download", data={"fmt": "epub"})

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_start_download_redirects_to_status_page(logged_in_client: TestClient) -> None:
    volumes = [Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1")])]
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(volumes)):
        response = logged_in_client.post(
            "/titles/6712--test-novel/download", data={"fmt": "epub"}
        )
        assert response.status_code == 303
        location = response.headers["location"]
        assert location.startswith("/titles/6712--test-novel/download/")

        job_id = _job_id_from_location(location)
        _wait_until_terminal(job_id)

    job = get_job(job_id)
    assert job is not None
    assert job.status == "done"
    assert job.completed == 1
    assert job.total == 1
    assert job.result_path is not None

    os.remove(job.result_path)


def test_start_download_rejects_unknown_format(logged_in_client: TestClient) -> None:
    response = logged_in_client.post(
        "/titles/6712--test-novel/download", data={"fmt": "docx"}
    )

    assert response.status_code == 400


def test_start_download_passes_translation_index_through(
    logged_in_client: TestClient,
) -> None:
    volumes = [Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1")])]
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(volumes)):
        response = logged_in_client.post(
            "/titles/6712--test-novel/download",
            data={"fmt": "txt", "translation_index": "1"},
        )
        job_id = _job_id_from_location(response.headers["location"])
        _wait_until_terminal(job_id)

    job = get_job(job_id)
    assert job is not None
    assert job.status == "done"

    os.remove(job.result_path)


def test_show_download_status_renders_running_progress() -> None:
    job = create_job("6712--test-novel", "epub")
    job.status = "running"
    job.completed = 3
    job.total = 10

    response = client.get(f"/titles/6712--test-novel/download/{job.id}")

    assert response.status_code == 200
    assert "Глава 3 из 10" in response.text
    assert "width: 30.0%" in response.text
    assert (
        f'data-status-url="/titles/6712--test-novel/download/{job.id}/status"'
        in response.text
    )
    assert "static/js/download-status.js" in response.text


def test_show_download_status_renders_done_with_file_link() -> None:
    job = create_job("6712--test-novel", "epub")
    job.status = "done"

    response = client.get(f"/titles/6712--test-novel/download/{job.id}")

    assert response.status_code == 200
    assert "Готово" in response.text
    assert f'href="/titles/6712--test-novel/download/{job.id}/file"' in response.text
    # a job that's already terminal on page load must not include the poller - see
    # download-status.js's docstring for why (an infinite reload loop otherwise).
    assert "static/js/download-status.js" not in response.text


def test_show_download_status_unknown_job_returns_404() -> None:
    response = client.get("/titles/6712--test-novel/download/does-not-exist")

    assert response.status_code == 404


def test_show_download_status_rejects_mismatched_slug_url() -> None:
    job = create_job("6712--test-novel", "epub")

    response = client.get(f"/titles/other-title/download/{job.id}")

    assert response.status_code == 404


def test_download_status_json_shape() -> None:
    job = create_job("6712--test-novel", "epub")
    job.status = "running"
    job.completed = 2
    job.total = 5

    response = client.get(f"/titles/6712--test-novel/download/{job.id}/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "running",
        "completed": 2,
        "total": 5,
        "error": None,
        "eta_seconds": None,  # no started_at set on this job - not enough signal yet
    }


def test_download_status_includes_eta_once_running() -> None:
    job = create_job("6712--test-novel", "epub")
    job.status = "running"
    job.started_at = time.monotonic() - 10  # 10s elapsed
    job.completed = 5
    job.total = 10

    response = client.get(f"/titles/6712--test-novel/download/{job.id}/status")

    # 5 chapters in 10s -> 0.5 chapters/s -> 5 remaining chapters -> 10s left
    assert response.json()["eta_seconds"] == pytest.approx(10.0, rel=0.1)


def test_download_result_file_not_ready_returns_404() -> None:
    job = create_job("6712--test-novel", "epub")
    job.status = "running"

    response = client.get(f"/titles/6712--test-novel/download/{job.id}/file")

    assert response.status_code == 404


def test_download_result_file_serves_and_cleans_up() -> None:
    job = create_job("6712--test-novel", "epub")
    job.status = "done"
    fd, path = tempfile.mkstemp(suffix=".epub")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write("exported content")
    job.result_path = Path(path)

    response = client.get(f"/titles/6712--test-novel/download/{job.id}/file")

    assert response.status_code == 200
    assert response.content == b"exported content"
    assert 'filename="6712--test-novel.epub"' in response.headers["content-disposition"]
    assert not os.path.exists(path)
    assert job.result_path is None


def test_download_result_file_repeat_request_returns_friendly_404() -> None:
    job = create_job("6712--test-novel", "epub")
    job.status = "done"
    fd, path = tempfile.mkstemp(suffix=".epub")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write("exported content")
    job.result_path = Path(path)

    first = client.get(f"/titles/6712--test-novel/download/{job.id}/file")
    assert first.status_code == 200

    second = client.get(f"/titles/6712--test-novel/download/{job.id}/file")

    assert second.status_code == 404
    assert "скачан" in second.json()["detail"]


def test_show_download_status_renders_translation_choice() -> None:
    job = create_job("6712--test-novel", "epub")
    job.status = "needs_translation"
    job.ambiguous_chapters = [
        AmbiguousChapter(
            volume="1",
            number="5",
            branches=[_branch(1, "Команда А"), _branch(2, "Команда Б")],
        ),
        AmbiguousChapter(volume="1", number="6", branches=[_branch(3, "Команда В")]),
    ]

    response = client.get(f"/titles/6712--test-novel/download/{job.id}")

    assert response.status_code == 200
    assert "Том 1, глава 5" in response.text
    assert "Том 1, глава 6" in response.text
    assert "Команда А" in response.text
    assert "Команда Б" in response.text
    assert 'action="/titles/6712--test-novel/download"' in response.text
    assert 'value="epub"' in response.text
    # max_branches across all ambiguous chapters is 2 (the first chapter's two branches)
    assert '<option value="0">Вариант 1</option>' in response.text
    assert '<option value="1">Вариант 2</option>' in response.text
    assert '<option value="2">' not in response.text
    # PR 54: the plain <select> is progressively enhanced into a custom listbox.
    assert "static/js/custom-dropdown.js" in response.text


def test_start_download_needs_translation_end_to_end(logged_in_client: TestClient) -> None:
    exc = MultipleTitleTranslationsError(
        "6712--test-novel",
        chapters=[
            AmbiguousChapter(volume="1", number="5", branches=[_branch(1), _branch(2)])
        ],
    )
    with patch("app.services.client.RanobeLib", return_value=_AmbiguousClient(exc)):
        response = logged_in_client.post(
            "/titles/6712--test-novel/download", data={"fmt": "epub"}
        )
        job_id = _job_id_from_location(response.headers["location"])
        _wait_until_terminal(job_id)

    job = get_job(job_id)
    assert job is not None
    assert job.status == "needs_translation"
    assert len(job.ambiguous_chapters) == 1

    status_response = logged_in_client.get(f"/titles/6712--test-novel/download/{job_id}")
    assert "выберите один" in status_response.text


def test_download_delivered_via_global_toast_after_leaving_job_page(
    logged_in_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR 50's actual bug scenario: start a download and never open (or leave) its own
    status page - the only page-scoped signal (download-status.js) never runs, so the file
    must still reach the visitor through GET /downloads/ready, polled from any page."""
    # _jobs is a process-wide dict (see app/jobs/store.py) shared with every other test in
    # this file - reset it so an earlier test's leftover "done" job for this same user_id=1
    # doesn't also show up as "ready" below.
    monkeypatch.setattr(job_store, "_jobs", {})
    volumes = [Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1")])]
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(volumes)):
        response = logged_in_client.post(
            "/titles/6712--test-novel/download", data={"fmt": "epub"}
        )
        job_id = _job_id_from_location(response.headers["location"])
        # Deliberately never GET the job's own status page or /status - simulating a
        # visitor who clicked "Скачать тайтл" and immediately navigated elsewhere.
        _wait_until_terminal(job_id)

    ready = logged_in_client.get("/downloads/ready")
    assert ready.status_code == 200
    body = ready.json()
    assert len(body) == 1
    assert body[0]["job_id"] == job_id
    assert body[0]["slug_url"] == "6712--test-novel"
    file_url = body[0]["file_url"]

    download = logged_in_client.get(file_url)
    assert download.status_code == 200
    assert download.content == b"exported content"

    # Delivered - no longer offered again, and a repeat click 404s cleanly rather than
    # erroring on the now-missing file.
    ready_after = logged_in_client.get("/downloads/ready")
    assert ready_after.json() == []
    repeat = logged_in_client.get(file_url)
    assert repeat.status_code == 404


def _second_logged_in_client() -> TestClient:
    """A second session in the same process/DB as `logged_in_client` (registers "bob" as
    a distinct user_id) - its own TestClient instance so it gets its own session cookie
    jar, independent from `logged_in_client`'s."""
    from app.main import app as fastapi_app

    other = TestClient(fastapi_app, follow_redirects=False)
    other.post(
        "/register",
        data={
            "email": "bob@example.com",
            "password": "hunter2pass",
            "password_confirm": "hunter2pass",
        },
    )
    return other


def test_show_download_status_rejects_job_owned_by_other_user(
    logged_in_client: TestClient,
) -> None:
    job = create_job("6712--test-novel", "epub", user_id=1)  # alice

    bob = _second_logged_in_client()
    response = bob.get(f"/titles/6712--test-novel/download/{job.id}")

    assert response.status_code == 403


def test_download_status_rejects_job_owned_by_other_user(
    logged_in_client: TestClient,
) -> None:
    job = create_job("6712--test-novel", "epub", user_id=1)  # alice

    bob = _second_logged_in_client()
    response = bob.get(f"/titles/6712--test-novel/download/{job.id}/status")

    assert response.status_code == 403


def test_download_result_file_rejects_job_owned_by_other_user(
    logged_in_client: TestClient,
) -> None:
    job = create_job("6712--test-novel", "epub", user_id=1)  # alice
    job.status = "done"
    fd, path = tempfile.mkstemp(suffix=".epub")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write("exported content")
    job.result_path = Path(path)

    bob = _second_logged_in_client()
    response = bob.get(f"/titles/6712--test-novel/download/{job.id}/file")

    assert response.status_code == 403
    # rejected before the file could be handed out - still there, and still fetchable by
    # the actual owner.
    assert job.result_path is not None
    os.remove(job.result_path)


def test_download_status_rejects_anonymous_visitor_for_owned_job(
    logged_in_client: TestClient,
) -> None:
    job = create_job("6712--test-novel", "epub", user_id=1)  # alice

    anonymous = TestClient(app, follow_redirects=False)
    response = anonymous.get(f"/titles/6712--test-novel/download/{job.id}/status")

    assert response.status_code == 403


def test_download_status_owner_still_sees_their_own_job(
    logged_in_client: TestClient,
) -> None:
    job = create_job("6712--test-novel", "epub", user_id=1)  # alice
    job.status = "running"
    job.completed = 1
    job.total = 4

    response = logged_in_client.get(f"/titles/6712--test-novel/download/{job.id}/status")

    assert response.status_code == 200


def test_download_status_anonymous_job_accessible_to_anyone(
    logged_in_client: TestClient,
) -> None:
    job = create_job("6712--test-novel", "epub")  # no user_id - anonymous download

    bob = _second_logged_in_client()
    response = bob.get(f"/titles/6712--test-novel/download/{job.id}/status")

    assert response.status_code == 200


async def test_start_download_records_history_for_logged_in_user(
    logged_in_client: TestClient,
) -> None:
    volumes = [Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1")])]
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(volumes)):
        response = logged_in_client.post(
            "/titles/6712--test-novel/download", data={"fmt": "epub"}
        )
        job_id = _job_id_from_location(response.headers["location"])
        _wait_until_terminal(job_id)

    os.remove(get_job(job_id).result_path)

    async with connection() as conn:
        entries = await list_download_history(conn, user_id=1)
    assert len(entries) == 1
    assert entries[0].slug_url == "6712--test-novel"
    assert entries[0].fmt == "epub"
    assert entries[0].status == "done"
    assert entries[0].chapter_count == 1

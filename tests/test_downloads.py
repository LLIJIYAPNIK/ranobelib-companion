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
from app.db.library import add_entry, set_default_translation_index
from app.jobs.store import create_job, get_job, list_active_jobs_for_user
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


# --- PR 205: start_download() falls back to library_entries.default_translation_index --


class _CapturingClient(_FakeClient):
    """Same as _FakeClient, but records the `translation_index` download_title() was
    actually called with, so a test can tell whether start_download() read the saved
    default rather than just asserting the job still finished."""

    def __init__(self, volumes: list[Volume], captured: dict[str, object]) -> None:
        super().__init__(volumes)
        self._captured = captured

    async def download_title(
        self,
        *,
        branch_id: int | None = None,
        translation_index: int | None = None,
        chapter_delay: float = 0.0,
        on_chapter: object = None,
    ) -> list[Volume]:
        self._captured["translation_index"] = translation_index
        return await super().download_title(
            branch_id=branch_id,
            translation_index=translation_index,
            chapter_delay=chapter_delay,
            on_chapter=on_chapter,
        )


async def test_start_download_uses_saved_default_translation_index(
    logged_in_client: TestClient,
) -> None:
    async with connection() as conn:
        await add_entry(conn, 1, "6712--test-novel")
        await set_default_translation_index(conn, 1, "6712--test-novel", 1)

    volumes = [Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1")])]
    captured: dict[str, object] = {}
    with patch(
        "app.services.client.RanobeLib", return_value=_CapturingClient(volumes, captured)
    ):
        response = logged_in_client.post(
            "/titles/6712--test-novel/download", data={"fmt": "epub"}
        )
        job_id = _job_id_from_location(response.headers["location"])
        _wait_until_terminal(job_id)

    assert captured["translation_index"] == 1
    os.remove(get_job(job_id).result_path)


async def test_start_download_explicit_translation_index_overrides_saved_default(
    logged_in_client: TestClient,
) -> None:
    async with connection() as conn:
        await add_entry(conn, 1, "6712--test-novel")
        await set_default_translation_index(conn, 1, "6712--test-novel", 1)

    volumes = [Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1")])]
    captured: dict[str, object] = {}
    with patch(
        "app.services.client.RanobeLib", return_value=_CapturingClient(volumes, captured)
    ):
        response = logged_in_client.post(
            "/titles/6712--test-novel/download",
            data={"fmt": "epub", "translation_index": "0"},
        )
        job_id = _job_id_from_location(response.headers["location"])
        _wait_until_terminal(job_id)

    assert captured["translation_index"] == 0  # the form's own value, not the saved "1"
    os.remove(get_job(job_id).result_path)


async def test_start_download_without_library_entry_passes_none(
    logged_in_client: TestClient,
) -> None:
    """No library entry at all for this title - not just "no default saved" - must not
    error out looking one up."""
    volumes = [Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1")])]
    captured: dict[str, object] = {}
    with patch(
        "app.services.client.RanobeLib", return_value=_CapturingClient(volumes, captured)
    ):
        response = logged_in_client.post(
            "/titles/6712--test-novel/download", data={"fmt": "epub"}
        )
        job_id = _job_id_from_location(response.headers["location"])
        _wait_until_terminal(job_id)

    assert captured["translation_index"] is None
    os.remove(get_job(job_id).result_path)


async def test_start_download_saved_default_that_no_longer_resolves_falls_back_to_prompt(
    logged_in_client: TestClient,
) -> None:
    """A default was saved earlier, but a chapter added since then has branches the saved
    index doesn't reach (see start_download()'s own comment) - must land on
    "needs_translation" like any other unresolved ambiguous chapter, not error out."""
    async with connection() as conn:
        await add_entry(conn, 1, "6712--test-novel")
        await set_default_translation_index(conn, 1, "6712--test-novel", 5)

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

    assert get_job(job_id).status == "needs_translation"


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
    # PR 208: resumes THIS job by id rather than start_download() (which would create a
    # second, separate one) - no more "fmt" hidden field either, since the resumed job
    # already carries its own.
    assert f'action="/titles/6712--test-novel/download/{job.id}/retry"' in response.text
    assert 'value="epub"' not in response.text
    # max_branches across all ambiguous chapters is 2 (the first chapter's two branches)
    assert '<option value="0">Вариант 1</option>' in response.text
    assert '<option value="1">Вариант 2</option>' in response.text
    assert '<option value="2">' not in response.text
    # PR 54: the plain <select> is progressively enhanced into a custom listbox.
    assert "static/js/custom-dropdown.js" in response.text
    # PR 206: the translation-choice form comes before the list of ambiguous chapters, not
    # after it - so picking a translation never requires scrolling past that list first.
    form_action = f'action="/titles/6712--test-novel/download/{job.id}/retry"'
    assert response.text.index(form_action) < response.text.index("download-status__ambiguous")


def test_show_download_status_translation_form_precedes_a_long_ambiguous_list() -> None:
    """PR 206's own scenario: a title with many ambiguous chapters (the roadmap's own
    screenshot has 21) - the form must still come first even when the list below it is
    long, not just when it's short enough that the difference is invisible."""
    job = create_job("6712--test-novel", "epub")
    job.status = "needs_translation"
    job.ambiguous_chapters = [
        AmbiguousChapter(volume="1", number=str(n), branches=[_branch(1), _branch(2)])
        for n in range(1, 22)
    ]

    response = client.get(f"/titles/6712--test-novel/download/{job.id}")

    assert response.status_code == 200
    assert response.text.count("download-status__ambiguous-item") == 21
    assert response.text.index(
        f'action="/titles/6712--test-novel/download/{job.id}/retry"'
    ) < response.text.index("download-status__ambiguous-item")


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


# --- PR 208: /titles/{slug_url}/download/{job_id}/retry resumes the SAME job ------------


class _ResolvingOnRetryClient:
    """download_title() raises `exc` unless called with `resolving_index` - the same
    "first attempt ambiguous, a later one with the right translation_index resolves it"
    shape the retry flow produces for a real title."""

    def __init__(
        self, volumes: list[Volume], exc: Exception, resolving_index: int | None
    ) -> None:
        self._volumes = volumes
        self._exc = exc
        self._resolving_index = resolving_index

    async def __aenter__(self) -> "_ResolvingOnRetryClient":
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
        if translation_index != self._resolving_index:
            raise self._exc
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


def _start_needs_translation_job(logged_in_client: TestClient, client_impl: object) -> str:
    with patch("app.services.client.RanobeLib", return_value=client_impl):
        response = logged_in_client.post(
            "/titles/6712--test-novel/download", data={"fmt": "epub"}
        )
        job_id = _job_id_from_location(response.headers["location"])
        _wait_until_terminal(job_id)
    assert get_job(job_id).status == "needs_translation"
    return job_id


def test_retry_requires_login() -> None:
    job = create_job("6712--test-novel", "epub", user_id=1)
    job.status = "needs_translation"

    response = client.post(
        f"/titles/6712--test-novel/download/{job.id}/retry",
        data={"translation_index": "0"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_retry_unknown_job_returns_404(logged_in_client: TestClient) -> None:
    response = logged_in_client.post(
        "/titles/6712--test-novel/download/does-not-exist/retry",
        data={"translation_index": "0"},
    )

    assert response.status_code == 404


def test_retry_rejects_job_owned_by_other_user(logged_in_client: TestClient) -> None:
    job = create_job("6712--test-novel", "epub", user_id=1)  # alice
    job.status = "needs_translation"

    bob = _second_logged_in_client()
    response = bob.post(
        f"/titles/6712--test-novel/download/{job.id}/retry",
        data={"translation_index": "0"},
    )

    assert response.status_code == 403


def test_retry_rejects_a_job_not_awaiting_translation(logged_in_client: TestClient) -> None:
    job = create_job("6712--test-novel", "epub", user_id=1)
    job.status = "running"

    response = logged_in_client.post(
        f"/titles/6712--test-novel/download/{job.id}/retry",
        data={"translation_index": "0"},
    )

    assert response.status_code == 409


def test_retry_resumes_the_same_job_instead_of_creating_a_second_one(
    logged_in_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # _jobs is a process-wide dict (see app/jobs/store.py) shared with every other test in
    # this file - reset it so an earlier test's own leftover job for this same user_id=1
    # doesn't inflate the "exactly one active job" count below.
    monkeypatch.setattr(job_store, "_jobs", {})
    exc = MultipleTitleTranslationsError(
        "6712--test-novel",
        chapters=[
            AmbiguousChapter(volume="1", number="5", branches=[_branch(1), _branch(2)])
        ],
    )
    volumes = [Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1")])]
    client_impl = _ResolvingOnRetryClient(volumes, exc, resolving_index=1)
    job_id = _start_needs_translation_job(logged_in_client, client_impl)
    job_before = get_job(job_id)
    # Exactly one job for this title in "Текущие" right after the first attempt landed on
    # needs_translation - the retry below must not add a second one alongside it.
    assert len(list_active_jobs_for_user(1)) == 1

    with patch("app.services.client.RanobeLib", return_value=client_impl):
        retry_response = logged_in_client.post(
            f"/titles/6712--test-novel/download/{job_id}/retry",
            data={"translation_index": "1"},
            follow_redirects=False,
        )
        _wait_until_terminal(job_id)

    assert retry_response.status_code == 303
    assert retry_response.headers["location"] == f"/titles/6712--test-novel/download/{job_id}"
    job_after = get_job(job_id)
    assert job_after is job_before  # same job object/id, not a second one
    assert job_after.status == "done"
    # Done now, not "active" - and no second, still-active job snuck in alongside it.
    assert list_active_jobs_for_user(1) == []
    os.remove(job_after.result_path)


def test_retry_that_still_cant_resolve_stays_on_needs_translation(
    logged_in_client: TestClient,
) -> None:
    """A retry whose translation_index doesn't cover every remaining ambiguous chapter -
    must land back on needs_translation like the very first attempt, not error out."""
    exc = MultipleTitleTranslationsError(
        "6712--test-novel",
        chapters=[
            AmbiguousChapter(volume="1", number="5", branches=[_branch(1), _branch(2)])
        ],
    )
    volumes = [Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1")])]
    # resolving_index=5 - no download_title() call in this test ever passes 5, so every
    # attempt (including the retry) raises MultipleTitleTranslationsError again.
    client_impl = _ResolvingOnRetryClient(volumes, exc, resolving_index=5)
    job_id = _start_needs_translation_job(logged_in_client, client_impl)

    with patch("app.services.client.RanobeLib", return_value=client_impl):
        retry_response = logged_in_client.post(
            f"/titles/6712--test-novel/download/{job_id}/retry",
            data={"translation_index": "1"},
            follow_redirects=False,
        )
        _wait_until_terminal(job_id)

    assert retry_response.status_code == 303
    assert get_job(job_id).status == "needs_translation"


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

import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from ranobelib import MultipleTitleTranslationsError
from ranobelib.exceptions import AmbiguousChapter
from ranobelib.models import Chapter, ChapterBranch, ChapterUser, Team, Volume

from app.jobs.store import create_job, get_job
from app.main import app

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


def test_start_download_redirects_to_status_page() -> None:
    volumes = [Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1")])]
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(volumes)):
        response = client.post(
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


def test_start_download_rejects_unknown_format() -> None:
    response = client.post(
        "/titles/6712--test-novel/download", data={"fmt": "docx"}
    )

    assert response.status_code == 400


def test_start_download_passes_translation_index_through() -> None:
    volumes = [Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1")])]
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(volumes)):
        response = client.post(
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


def test_show_download_status_renders_done_with_file_link() -> None:
    job = create_job("6712--test-novel", "epub")
    job.status = "done"

    response = client.get(f"/titles/6712--test-novel/download/{job.id}")

    assert response.status_code == 200
    assert "Готово" in response.text
    assert f'href="/titles/6712--test-novel/download/{job.id}/file"' in response.text


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
    }


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


def test_start_download_needs_translation_end_to_end() -> None:
    exc = MultipleTitleTranslationsError(
        "6712--test-novel",
        chapters=[
            AmbiguousChapter(volume="1", number="5", branches=[_branch(1), _branch(2)])
        ],
    )
    with patch("app.services.client.RanobeLib", return_value=_AmbiguousClient(exc)):
        response = client.post(
            "/titles/6712--test-novel/download", data={"fmt": "epub"}
        )

    job_id = _job_id_from_location(response.headers["location"])
    _wait_until_terminal(job_id)
    job = get_job(job_id)
    assert job is not None
    assert job.status == "needs_translation"
    assert len(job.ambiguous_chapters) == 1

    status_response = client.get(f"/titles/6712--test-novel/download/{job_id}")
    assert "выберите один" in status_response.text

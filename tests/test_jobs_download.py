import os
from datetime import UTC, datetime

import pytest
from ranobelib import (
    AuthRequiredError,
    DownloadTitleInterruptedError,
    MultipleTitleTranslationsError,
    RateLimitError,
    TitleNotFoundError,
)
from ranobelib.exceptions import AmbiguousChapter
from ranobelib.models import Chapter, ChapterBranch, ChapterUser, Volume

from app.jobs.download import run_download_job
from app.jobs.models import DownloadJob


class _FakeClient:
    def __init__(
        self, volumes: list[Volume] | None = None, exc: Exception | None = None
    ) -> None:
        self._volumes = volumes or []
        self._exc = exc
        self.export_calls: list[tuple[list[Chapter], str, str]] = []
        self.download_kwargs: dict[str, object] | None = None

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
        self.download_kwargs = {"branch_id": branch_id, "translation_index": translation_index}
        if self._exc is not None:
            raise self._exc
        chapters = [chapter for volume in self._volumes for chapter in volume.chapters]
        total = len(chapters)
        for index in range(total):
            if on_chapter is not None:
                on_chapter(index + 1, total)
        return self._volumes

    async def export(self, chapters: list[Chapter], *, fmt: str, path: str) -> str:
        self.export_calls.append((chapters, fmt, path))
        with open(path, "w", encoding="utf-8") as f:
            f.write("exported content")
        return path


class _BoomClient(_FakeClient):
    async def download_title(self, **kwargs: object) -> list[Volume]:
        raise RuntimeError("boom")


def _job() -> DownloadJob:
    return DownloadJob(id="job-1", slug_url="6712--test-novel", fmt="epub")


def _branch(branch_id: int) -> ChapterBranch:
    return ChapterBranch(
        id=branch_id,
        branch_id=branch_id,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        teams=[],
        user=ChapterUser(id=branch_id, username=f"user{branch_id}"),
    )


async def test_run_download_job_completes_successfully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chapters = [Chapter(id=1, volume="1", number="1", content="<p>a</p>")]
    volumes = [Volume(number="1", chapters=chapters)]
    fake = _FakeClient(volumes=volumes)
    monkeypatch.setattr("app.jobs.download.open_client", lambda slug_url: fake)

    job = _job()
    await run_download_job(job)

    assert job.status == "done"
    assert job.completed == 1
    assert job.total == 1
    assert job.result_path is not None
    assert job.result_path.read_text(encoding="utf-8") == "exported content"
    assert len(fake.export_calls) == 1
    assert fake.export_calls[0][0] == chapters
    assert fake.export_calls[0][1] == "epub"

    os.remove(job.result_path)


async def test_run_download_job_reports_progress_via_on_chapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chapters = [
        Chapter(id=1, volume="1", number="1"),
        Chapter(id=2, volume="1", number="2"),
        Chapter(id=3, volume="1", number="3"),
    ]
    volumes = [Volume(number="1", chapters=chapters)]
    fake = _FakeClient(volumes=volumes)
    monkeypatch.setattr("app.jobs.download.open_client", lambda slug_url: fake)

    job = _job()
    await run_download_job(job)

    assert job.completed == 3
    assert job.total == 3

    assert job.result_path is not None
    os.remove(job.result_path)


async def test_run_download_job_passes_translation_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    volumes = [Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1")])]
    fake = _FakeClient(volumes=volumes)
    monkeypatch.setattr("app.jobs.download.open_client", lambda slug_url: fake)

    job = _job()
    await run_download_job(job, translation_index=2)

    assert fake.download_kwargs == {"branch_id": None, "translation_index": 2}

    assert job.result_path is not None
    os.remove(job.result_path)


async def test_run_download_job_needs_translation(monkeypatch: pytest.MonkeyPatch) -> None:
    ambiguous = [AmbiguousChapter(volume="1", number="5", branches=[_branch(1), _branch(2)])]
    exc = MultipleTitleTranslationsError("6712--test-novel", chapters=ambiguous)
    fake = _FakeClient(exc=exc)
    monkeypatch.setattr("app.jobs.download.open_client", lambda slug_url: fake)

    job = _job()
    await run_download_job(job)

    assert job.status == "needs_translation"
    assert job.ambiguous_chapters == ambiguous
    assert fake.export_calls == []
    assert job.result_path is None


async def test_run_download_job_maps_known_sdk_error(monkeypatch: pytest.MonkeyPatch) -> None:
    exc = TitleNotFoundError("6712--missing")
    fake = _FakeClient(exc=exc)
    monkeypatch.setattr("app.jobs.download.open_client", lambda slug_url: fake)

    job = _job()
    await run_download_job(job)

    assert job.status == "error"
    assert job.error == "Тайтл не найден, проверьте ссылку"


async def test_run_download_job_maps_auth_required(monkeypatch: pytest.MonkeyPatch) -> None:
    exc = AuthRequiredError("https://ranobelib.me/x")
    fake = _FakeClient(exc=exc)
    monkeypatch.setattr("app.jobs.download.open_client", lambda slug_url: fake)

    job = _job()
    await run_download_job(job)

    assert job.status == "error"
    assert job.error == "Требуется авторизация — недоступно"


async def test_run_download_job_maps_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # A bare RateLimitError (not wrapped in DownloadTitleInterruptedError) models the API
    # rate-limiting the chapter-list fetch itself, before download_title()'s per-chapter
    # loop - and therefore before its own bulk rate-limit retry budget - even starts.
    exc = RateLimitError(retry_after=30)
    fake = _FakeClient(exc=exc)
    monkeypatch.setattr("app.jobs.download.open_client", lambda slug_url: fake)

    job = _job()
    await run_download_job(job)

    assert job.status == "error"
    assert job.error == "ranobelib сейчас ограничивает запросы, попробуйте позже"


async def test_run_download_job_maps_download_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PR 20: a long title's sequential fetch can still exhaust download_title()'s own
    # bulk rate-limit retry budget (ranobelib-python-sdk#41). The SDK no longer discards
    # progress in that case - it wraps whatever was already fetched in
    # DownloadTitleInterruptedError, which the job runner must unwrap: report the
    # *cause* (not this wrapper, which isn't in the CLAUDE.md error table) plus how far it
    # got, and keep job.completed/job.total at the partial progress already ticked.
    class _InterruptedClient(_FakeClient):
        async def download_title(
            self, *, on_chapter: object = None, **kwargs: object
        ) -> list[Volume]:
            if on_chapter is not None:
                on_chapter(2, 5)
            try:
                raise RateLimitError(retry_after=None)
            except RateLimitError as cause:
                raise DownloadTitleInterruptedError(
                    "6712--test-novel",
                    volumes=[Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1")])],
                    completed=2,
                    total=5,
                ) from cause

    fake = _InterruptedClient()
    monkeypatch.setattr("app.jobs.download.open_client", lambda slug_url: fake)

    job = _job()
    await run_download_job(job)

    assert job.status == "error"
    assert job.completed == 2
    assert job.total == 5
    assert job.error == (
        "ranobelib сейчас ограничивает запросы, попробуйте позже (скачано 2 из 5 глав)"
    )
    assert fake.export_calls == []
    assert job.result_path is None


async def test_run_download_job_maps_unexpected_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _BoomClient()
    monkeypatch.setattr("app.jobs.download.open_client", lambda slug_url: fake)

    job = _job()
    await run_download_job(job)

    assert job.status == "error"
    assert job.error == "Внутренняя ошибка, попробуйте позже"


async def test_run_download_job_malformed_slug_url_is_a_friendly_error() -> None:
    # No open_client patch here on purpose - real get_client()/RanobeLib(...) raises a
    # plain ValueError for this slug, which open_client() must convert to
    # TitleNotFoundError rather than leaving the runner's except Exception fallback (a
    # generic "internal error") to catch it.
    job = DownloadJob(id="job-1", slug_url="not-a-valid-slug", fmt="epub")

    await run_download_job(job)

    assert job.status == "error"
    assert job.error == "Тайтл не найден, проверьте ссылку"

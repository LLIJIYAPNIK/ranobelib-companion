import os
from datetime import UTC, datetime

import pytest
from ranobelib import (
    AuthRequiredError,
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
        self,
        volumes: list[Volume] | None = None,
        exc: Exception | None = None,
        fail_times: int | None = None,
    ) -> None:
        self._volumes = volumes or []
        self._exc = exc
        # None means "every call fails" (the old, unconditional behavior); an int caps how
        # many of the first calls fail before download_title() starts succeeding, for
        # exercising _download_title_with_retries()'s recovery path.
        self._fail_times = fail_times
        self.export_calls: list[tuple[list[Chapter], str, str]] = []
        self.download_kwargs: dict[str, object] | None = None
        self.download_calls = 0

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
        self.download_calls += 1
        self.download_kwargs = {"branch_id": branch_id, "translation_index": translation_index}
        if self._exc is not None and (
            self._fail_times is None or self.download_calls <= self._fail_times
        ):
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
    # A title where the rate limit never lets up: _download_title_with_retries() should
    # still give up eventually (after _RATE_LIMIT_RETRIES retries) and surface the same
    # "error" status as before this retry loop existed, not hang or retry forever.
    exc = RateLimitError(retry_after=30)
    fake = _FakeClient(exc=exc)
    monkeypatch.setattr("app.jobs.download.open_client", lambda slug_url: fake)
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    job = _job()
    await run_download_job(job, sleep=fake_sleep)

    assert job.status == "error"
    assert job.error == "ranobelib сейчас ограничивает запросы, попробуйте позже"
    assert sleeps == [30, 30, 30, 30, 30]
    assert fake.download_calls == 6


async def test_run_download_job_recovers_after_transient_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reproduces PR 20: a long title's sequential fetch outlasts ApiClient's own retry
    # budget and gets a RateLimitError partway through. RanobeLib.download_title() itself
    # has no memory of where it stopped, but its disk cache means retrying the whole call
    # is cheap - already-fetched chapters come back from cache, so this must recover into
    # a normal "done" job rather than surfacing as a terminal error.
    chapters = [Chapter(id=1, volume="1", number="1")]
    volumes = [Volume(number="1", chapters=chapters)]
    exc = RateLimitError(retry_after=None)
    fake = _FakeClient(volumes=volumes, exc=exc, fail_times=2)
    monkeypatch.setattr("app.jobs.download.open_client", lambda slug_url: fake)
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    job = _job()
    await run_download_job(job, sleep=fake_sleep)

    assert job.status == "done"
    assert fake.download_calls == 3
    # No Retry-After header on either failure, so both back off by the fallback delay.
    assert sleeps == [30, 30]

    assert job.result_path is not None
    os.remove(job.result_path)


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

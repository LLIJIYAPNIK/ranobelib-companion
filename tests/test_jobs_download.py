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
        self,
        volumes: list[Volume] | None = None,
        exc: Exception | None = None,
        fail_times: int | None = None,
    ) -> None:
        self._volumes = volumes or []
        self._exc = exc
        # None means "every call fails" (the old, unconditional behavior); an int caps how
        # many of the first calls fail before download_title() starts succeeding, for
        # exercising _download_title_riding_out_rate_limits()'s recovery path.
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
        chapter_delay: float = 0.0,
        on_chapter: object = None,
    ) -> list[Volume]:
        self.download_calls += 1
        self.download_kwargs = {
            "branch_id": branch_id,
            "translation_index": translation_index,
            "chapter_delay": chapter_delay,
        }
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


def _job(user_id: int | None = None) -> DownloadJob:
    return DownloadJob(id="job-1", slug_url="6712--test-novel", fmt="epub", user_id=user_id)


def _branch(branch_id: int) -> ChapterBranch:
    return ChapterBranch(
        id=branch_id,
        branch_id=branch_id,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        teams=[],
        user=ChapterUser(id=branch_id, username=f"user{branch_id}"),
    )


def _interrupted(
    slug_url: str, *, completed: int, total: int, cause: Exception
) -> DownloadTitleInterruptedError:
    """Build a `DownloadTitleInterruptedError` with `cause` as its `__cause__`, the way
    `download_title()` itself raises one (`raise ... from cause`)."""
    volumes = (
        [Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1")])]
        if completed
        else []
    )
    try:
        raise cause
    except Exception as raised:
        exc = DownloadTitleInterruptedError(
            slug_url, volumes=volumes, completed=completed, total=total
        )
        exc.__cause__ = raised
        return exc


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


async def test_run_download_job_passes_translation_index_and_chapter_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    volumes = [Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1")])]
    fake = _FakeClient(volumes=volumes)
    monkeypatch.setattr("app.jobs.download.open_client", lambda slug_url: fake)

    job = _job()
    await run_download_job(job, translation_index=2)

    assert fake.download_kwargs is not None
    assert fake.download_kwargs["branch_id"] is None
    assert fake.download_kwargs["translation_index"] == 2
    # PR 20: be gentler on the API for a long title's sequential fetch, so it gets rate
    # limited less often in the first place rather than only leaning on retries to
    # recover from it - keep this in sync with app/jobs/download.py's _CHAPTER_DELAY.
    assert fake.download_kwargs["chapter_delay"] == 0.5

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


async def test_run_download_job_maps_rate_limit_after_retries_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A title where the rate limit never lets up: the job runner should still give up
    # eventually (after _RATE_LIMIT_RETRIES whole-call retries) and surface the usual
    # "error" status, not hang or retry forever.
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
    assert sleeps == [30] * 10
    assert fake.download_calls == 11


async def test_run_download_job_recovers_after_transient_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PR 20 follow-up: previously a rate-limited download just ended in "error" and the
    # user had to go back to the title page and click "Скачать тайтл" again. Now the job
    # runner retries the whole download_title() call itself - cheap even for a long title,
    # since RanobeLib's disk cache serves already-fetched chapters back for free - so it
    # recovers into "done" on its own instead.
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


async def test_run_download_job_recovers_after_download_interrupted_by_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same recovery, but via DownloadTitleInterruptedError - the shape download_title()
    # actually raises once its own internal rate-limit retry budget
    # (max_rate_limit_retries) is exhausted partway through the per-chapter loop.
    chapters = [Chapter(id=1, volume="1", number="1")]
    volumes = [Volume(number="1", chapters=chapters)]
    exc = _interrupted("6712--test-novel", completed=2, total=5, cause=RateLimitError())
    fake = _FakeClient(volumes=volumes, exc=exc, fail_times=1)
    monkeypatch.setattr("app.jobs.download.open_client", lambda slug_url: fake)
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    job = _job()
    await run_download_job(job, sleep=fake_sleep)

    assert job.status == "done"
    assert fake.download_calls == 2
    assert sleeps == [30]

    assert job.result_path is not None
    os.remove(job.result_path)


async def test_run_download_job_does_not_retry_download_interrupted_by_other_causes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A DownloadTitleInterruptedError NOT caused by rate limiting (e.g. a chapter that
    # turns out to need auth) isn't something waiting and retrying would fix - it must
    # surface immediately as an error, not burn through the whole retry budget first.
    exc = _interrupted(
        "6712--test-novel",
        completed=2,
        total=5,
        cause=AuthRequiredError("https://ranobelib.me/x"),
    )
    fake = _FakeClient(exc=exc)
    monkeypatch.setattr("app.jobs.download.open_client", lambda slug_url: fake)
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    job = _job()
    await run_download_job(job, sleep=fake_sleep)

    assert job.status == "error"
    assert job.completed == 0  # on_chapter() was never called by this fake
    assert fake.download_calls == 1
    assert sleeps == []
    assert job.error == "Требуется авторизация — недоступно (скачано 2 из 5 глав)"


async def test_run_download_job_maps_unexpected_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _BoomClient()
    monkeypatch.setattr("app.jobs.download.open_client", lambda slug_url: fake)

    job = _job()
    await run_download_job(job)

    assert job.status == "error"
    assert job.error == "Внутренняя ошибка, попробуйте позже"


async def test_run_download_job_records_its_own_job_id_in_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PR 58: the history row needs the job's id back to know whether the exported file is
    # still on disk (app/jobs/store.py's ready_file_url()) - without it, the "Скачать"
    # button on that row would have no job to look up.
    chapters = [Chapter(id=1, volume="1", number="1")]
    volumes = [Volume(number="1", chapters=chapters)]
    fake = _FakeClient(volumes=volumes)
    monkeypatch.setattr("app.jobs.download.open_client", lambda slug_url: fake)
    recorded: dict[str, object] = {}

    def fake_record_download(*args: object, **kwargs: object) -> None:
        recorded["args"] = args
        recorded["kwargs"] = kwargs

    monkeypatch.setattr("app.jobs.download.record_download", fake_record_download)

    job = _job(user_id=1)
    await run_download_job(job)

    assert recorded["kwargs"] == {"job_id": "job-1"}

    os.remove(job.result_path)


async def test_run_download_job_malformed_slug_url_is_a_friendly_error() -> None:
    # No open_client patch here on purpose - real get_client()/RanobeLib(...) raises a
    # plain ValueError for this slug, which open_client() must convert to
    # TitleNotFoundError rather than leaving the runner's except Exception fallback (a
    # generic "internal error") to catch it.
    job = DownloadJob(id="job-1", slug_url="not-a-valid-slug", fmt="epub")

    await run_download_job(job)

    assert job.status == "error"
    assert job.error == "Тайтл не найден, проверьте ссылку"

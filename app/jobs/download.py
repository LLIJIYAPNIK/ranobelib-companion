"""Runs a whole-title download_title() + export() as a background job."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from ranobelib import (
    DownloadTitleInterruptedError,
    MultipleTitleTranslationsError,
    RanobeLibError,
    RateLimitError,
)
from ranobelib.models import Volume

from app.db.connection import connection
from app.db.downloads import record_download
from app.exceptions import build_error_response
from app.jobs.models import DownloadJob, JobStatus
from app.services.client import open_client
from app.services.exports import temp_export_path

logger = logging.getLogger(__name__)

_TICK_HISTORY_SIZE = 10
"""How many recent on_chapter() ticks app/jobs/eta.py's estimate looks at - a small
window so the estimate tracks the *current* pace rather than the whole run's average."""

_CHAPTER_DELAY = 0.5
"""Extra delay, in seconds, download_title() waits after each chapter on top of
ApiClient's own pacing (see ranobelib-python-sdk#41) - roughly triples the interval
between chapter requests, to be hit rate-limited less often on a long title in the first
place rather than only leaning on retries to recover from it."""

_RATE_LIMIT_RETRIES = 10
"""How many times a whole `download_title()` call is retried when it's interrupted by
sustained rate limiting that outlasted even the SDK's own internal retry budget
(`download_title(max_rate_limit_retries=...)`, default 6 - see ranobelib-python-sdk#41).

This is what lets a job recover on its own instead of ending in "error" and requiring the
user to come back and click "Скачать тайтл" again: `RanobeLib`'s disk cache serves
already-fetched chapters back for free, so retrying the whole call resumes close to where
`DownloadTitleInterruptedError` left off rather than restarting the title, and stays cheap
even for a title long enough to need every one of these retries."""

_RATE_LIMIT_FALLBACK_BACKOFF = 30.0
"""Backoff, in seconds, before retrying a RateLimitError with no `Retry-After` header."""


class _DownloadTitleClient(Protocol):
    async def download_title(
        self,
        *,
        branch_id: int | None,
        translation_index: int | None,
        chapter_delay: float,
        on_chapter: Callable[[int, int], None] | None,
    ) -> list[Volume]: ...


async def run_download_job(
    job: DownloadJob,
    *,
    branch_id: int | None = None,
    translation_index: int | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Fetch every chapter of `job.slug_url` and export them into one file, updating
    `job` in place as it goes - this is what the status endpoint polls."""
    job.status = "running"
    job.started_at = time.monotonic()

    def on_chapter(completed: int, total: int) -> None:
        job.completed = completed
        job.total = total
        job.recent_ticks.append((time.monotonic(), completed))
        if len(job.recent_ticks) > _TICK_HISTORY_SIZE:
            job.recent_ticks.pop(0)

    try:
        async with open_client(job.slug_url) as lib:
            volumes = await _download_title_riding_out_rate_limits(
                lib,
                slug_url=job.slug_url,
                branch_id=branch_id,
                translation_index=translation_index,
                on_chapter=on_chapter,
                sleep=sleep,
            )
            chapters = [chapter for volume in volumes for chapter in volume.chapters]
            job.status = "exporting"
            with temp_export_path(job.fmt) as path:
                await lib.export(chapters, fmt=job.fmt, path=path)
        job.result_path = Path(path)
        # Writes the history row (if any - see _record_history()'s own docstring) before
        # job.status ever reports "done" to an external poller (GET .../status): otherwise
        # a client that saw "done" and immediately loaded GET /downloads could find the
        # job's own history entry not there yet - _record_history() now checks a
        # connection out of a real network pool, an actual await point a synchronous
        # sqlite3/psycopg write never had, unlike setting `job.status` itself, which is
        # just an in-memory attribute write with no such gap.
        await _record_history(job, "done", chapter_count=len(chapters))
        job.status = "done"
    except MultipleTitleTranslationsError as exc:
        # Not a terminal state - the retry form on download_status.html re-POSTs with a
        # translation_index, so no history entry yet.
        job.status = "needs_translation"
        job.ambiguous_chapters = exc.chapters
    except DownloadTitleInterruptedError as exc:
        # Only reached once _download_title_riding_out_rate_limits() has given up - either
        # the cause wasn't rate limiting (nothing an automatic retry would fix) or
        # _RATE_LIMIT_RETRIES whole-call retries weren't enough. job.completed/job.total
        # already reflect exc.completed/exc.total - on_chapter() was called for each of
        # them before the underlying error hit. Map the *cause* (what actually stopped the
        # download) for the user-facing message, not this wrapper - it isn't itself in the
        # CLAUDE.md error table.
        cause = exc.__cause__
        logger.warning(
            "download_title() for %s interrupted after %d/%d chapters",
            job.slug_url,
            exc.completed,
            exc.total,
            exc_info=exc,
        )
        detail = (
            build_error_response(cause).content["detail"]
            if isinstance(cause, RanobeLibError)
            else "Внутренняя ошибка, попробуйте позже"
        )
        job.error = f"{detail} (скачано {exc.completed} из {exc.total} глав)"
        await _record_history(job, "error", chapter_count=exc.completed)
        job.status = "error"
    except RanobeLibError as exc:
        logger.warning("%s downloading %s", type(exc).__name__, job.slug_url, exc_info=exc)
        job.error = build_error_response(exc).content["detail"]
        await _record_history(job, "error")
        job.status = "error"
    except Exception:
        logger.exception("Unexpected error downloading %s", job.slug_url)
        job.error = "Внутренняя ошибка, попробуйте позже"
        await _record_history(job, "error")
        job.status = "error"


async def _download_title_riding_out_rate_limits(
    lib: _DownloadTitleClient,
    *,
    slug_url: str,
    branch_id: int | None,
    translation_index: int | None,
    on_chapter: Callable[[int, int], None],
    sleep: Callable[[float], Awaitable[None]],
) -> list[Volume]:
    """`lib.download_title(...)`, automatically retried when rate limiting interrupts it -
    see `_RATE_LIMIT_RETRIES`. The job stays "running" (not "error") while this waits and
    retries, so the user never has to notice or come back and restart it by hand."""
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        try:
            return await lib.download_title(
                branch_id=branch_id,
                translation_index=translation_index,
                chapter_delay=_CHAPTER_DELAY,
                on_chapter=on_chapter,
            )
        except DownloadTitleInterruptedError as exc:
            cause = exc.__cause__
            if not isinstance(cause, RateLimitError) or attempt >= _RATE_LIMIT_RETRIES:
                raise
            delay = _rate_limit_delay(cause)
            logger.info(
                "Rate limited downloading %s (%d/%d chapters), retrying in %.0fs "
                "(attempt %d/%d)",
                slug_url,
                exc.completed,
                exc.total,
                delay,
                attempt + 1,
                _RATE_LIMIT_RETRIES,
            )
            await sleep(delay)
        except RateLimitError as exc:
            # Rate limited before the per-chapter loop even started (e.g. fetching the
            # chapter list itself), so there's no DownloadTitleInterruptedError to unwrap.
            if attempt >= _RATE_LIMIT_RETRIES:
                raise
            delay = _rate_limit_delay(exc)
            logger.info(
                "Rate limited fetching %s, retrying in %.0fs (attempt %d/%d)",
                slug_url,
                delay,
                attempt + 1,
                _RATE_LIMIT_RETRIES,
            )
            await sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def _rate_limit_delay(exc: RateLimitError) -> float:
    return _RATE_LIMIT_FALLBACK_BACKOFF if exc.retry_after is None else exc.retry_after


async def _record_history(
    job: DownloadJob, status: JobStatus, chapter_count: int | None = None
) -> None:
    """Called exactly once, at each point `job` reaches a terminal state (done or error) -
    also where `finished_at` gets set, the basis for sweep_expired_result_files() cleaning
    up an exported file nobody came back to download. The history write itself is a no-op
    for an anonymous download (no user_id, see create_job()), but the file it produced
    still needs sweeping regardless.

    Takes the terminal `status` as an explicit parameter rather than reading `job.status`
    - callers await this *before* setting `job.status` themselves, so a poller can never
    observe a terminal `job.status` before the history row it implies actually exists (see
    each call site's own comment on why that ordering matters now that this write goes
    over a real connection pool instead of a synchronous, gap-free call).

    Checks out its own connection via `connection()` rather than a route's own
    `Depends(get_connection)` - this whole function runs inside a background
    ``asyncio.create_task()`` (see app/api/downloads.py's `start_download()`) that outlives
    the request that started it, so there's no FastAPI request/dependency scope left to
    resolve one from by the time this runs."""
    job.finished_at = time.monotonic()
    if job.user_id is None:
        return
    async with connection() as conn:
        await record_download(
            conn,
            job.user_id,
            job.slug_url,
            job.fmt,
            status,
            chapter_count,
            job.error,
            job_id=job.id,
        )

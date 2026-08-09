"""Runs a whole-title download_title() + export() as a background job."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from ranobelib import MultipleTitleTranslationsError, RanobeLibError, RateLimitError
from ranobelib.models import Volume

from app.db.connection import get_connection
from app.db.downloads import record_download
from app.exceptions import build_error_response
from app.jobs.models import DownloadJob
from app.services.client import open_client
from app.services.exports import temp_export_path

logger = logging.getLogger(__name__)

_TICK_HISTORY_SIZE = 10
"""How many recent on_chapter() ticks app/jobs/eta.py's estimate looks at - a small
window so the estimate tracks the *current* pace rather than the whole run's average."""

_RATE_LIMIT_RETRIES = 5
"""How many times a whole `download_title()` call is retried after `RateLimitError`, on
top of `ApiClient`'s own per-request retries.

TODO(ranobelib-python-sdk#41): temporary workaround for a long title's sequential download
reliably outlasting `ApiClient`'s per-request retry budget (tuned for one request, not an
hours-long bulk download) and getting 429'd outright - without this, `RateLimitError`
propagates out of `download_title()` and aborts the whole job, discarding every chapter
already fetched. Remove once the SDK handles sustained rate limiting during bulk downloads
itself: https://github.com/LLIJIYAPNIK/ranobelib-python-sdk/issues/41

Retrying the whole call stays cheap even for long titles because `RanobeLib`'s disk cache
makes already-fetched chapters free to re-request, so each retry only pays for chapters it
hasn't fetched yet - this is a point re-fetch of what's missing, not a fresh download."""

_RATE_LIMIT_FALLBACK_BACKOFF = 30.0
"""Backoff, in seconds, before retrying a RateLimitError with no `Retry-After` header."""


class _DownloadTitleClient(Protocol):
    async def download_title(
        self,
        *,
        branch_id: int | None,
        translation_index: int | None,
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
            volumes = await _download_title_with_retries(
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
        job.status = "done"
        _record_history(job, chapter_count=len(chapters))
    except MultipleTitleTranslationsError as exc:
        # Not a terminal state - the retry form on download_status.html re-POSTs with a
        # translation_index, so no history entry yet.
        job.status = "needs_translation"
        job.ambiguous_chapters = exc.chapters
    except RanobeLibError as exc:
        logger.warning("%s downloading %s", type(exc).__name__, job.slug_url, exc_info=exc)
        job.status = "error"
        job.error = build_error_response(exc).content["detail"]
        _record_history(job)
    except Exception:
        logger.exception("Unexpected error downloading %s", job.slug_url)
        job.status = "error"
        job.error = "Внутренняя ошибка, попробуйте позже"
        _record_history(job)


async def _download_title_with_retries(
    lib: _DownloadTitleClient,
    *,
    slug_url: str,
    branch_id: int | None,
    translation_index: int | None,
    on_chapter: Callable[[int, int], None],
    sleep: Callable[[float], Awaitable[None]],
) -> list[Volume]:
    """`lib.download_title(...)`, retried on `RateLimitError` - see `_RATE_LIMIT_RETRIES`."""
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        try:
            return await lib.download_title(
                branch_id=branch_id,
                translation_index=translation_index,
                on_chapter=on_chapter,
            )
        except RateLimitError as exc:
            if attempt >= _RATE_LIMIT_RETRIES:
                raise
            delay = _RATE_LIMIT_FALLBACK_BACKOFF if exc.retry_after is None else exc.retry_after
            logger.info(
                "Rate limited downloading %s, retrying in %.0fs (attempt %d/%d)",
                slug_url,
                delay,
                attempt + 1,
                _RATE_LIMIT_RETRIES,
            )
            await sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def _record_history(job: DownloadJob, chapter_count: int | None = None) -> None:
    """No-op for an anonymous download (no user_id) - see create_job()."""
    if job.user_id is None:
        return
    record_download(
        get_connection(), job.user_id, job.slug_url, job.fmt, job.status, chapter_count, job.error
    )

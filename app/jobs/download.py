"""Runs a whole-title download_title() + export() as a background job."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from ranobelib import MultipleTitleTranslationsError, RanobeLibError

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


async def run_download_job(
    job: DownloadJob,
    *,
    branch_id: int | None = None,
    translation_index: int | None = None,
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
            volumes = await lib.download_title(
                branch_id=branch_id,
                translation_index=translation_index,
                on_chapter=on_chapter,
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


def _record_history(job: DownloadJob, chapter_count: int | None = None) -> None:
    """No-op for an anonymous download (no user_id) - see create_job()."""
    if job.user_id is None:
        return
    record_download(
        get_connection(), job.user_id, job.slug_url, job.fmt, job.status, chapter_count, job.error
    )

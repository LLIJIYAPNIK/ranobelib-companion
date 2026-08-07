"""Runs a whole-title download_title() + export() as a background job."""

from __future__ import annotations

import logging
from pathlib import Path

from ranobelib import MultipleTitleTranslationsError, RanobeLibError

from app.exceptions import build_error_response
from app.jobs.models import DownloadJob
from app.services.client import open_client
from app.services.exports import temp_export_path

logger = logging.getLogger(__name__)


async def run_download_job(
    job: DownloadJob,
    *,
    branch_id: int | None = None,
    translation_index: int | None = None,
) -> None:
    """Fetch every chapter of `job.slug_url` and export them into one file, updating
    `job` in place as it goes - this is what the status endpoint polls."""
    job.status = "running"

    def on_chapter(completed: int, total: int) -> None:
        job.completed = completed
        job.total = total

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
    except MultipleTitleTranslationsError as exc:
        job.status = "needs_translation"
        job.ambiguous_chapters = exc.chapters
    except RanobeLibError as exc:
        logger.warning("%s downloading %s", type(exc).__name__, job.slug_url, exc_info=exc)
        job.status = "error"
        job.error = build_error_response(exc).content["detail"]
    except Exception:
        logger.exception("Unexpected error downloading %s", job.slug_url)
        job.status = "error"
        job.error = "Внутренняя ошибка, попробуйте позже"

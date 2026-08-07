"""Starting and tracking a whole-title background download job."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Form
from fastapi.responses import RedirectResponse

from app.jobs.download import run_download_job
from app.jobs.store import create_job, track_task
from app.services.exports import require_known_format

router = APIRouter(prefix="/titles/{slug_url}/download")


@router.post("")
async def start_download(
    slug_url: str,
    fmt: Annotated[str, Form()],
    translation_index: Annotated[int | None, Form()] = None,
) -> RedirectResponse:
    require_known_format(fmt)
    job = create_job(slug_url, fmt)
    task = asyncio.create_task(
        run_download_job(job, translation_index=translation_index)
    )
    track_task(job.id, task)
    return RedirectResponse(f"/titles/{slug_url}/download/{job.id}", status_code=303)

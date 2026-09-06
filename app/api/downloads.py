"""Starting and tracking a whole-title background download job."""

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from psycopg import AsyncConnection
from starlette.background import BackgroundTask

from app.auth.dependencies import get_current_user, require_current_user
from app.db.connection import get_connection
from app.db.library import get_entry
from app.db.users import User
from app.jobs.download import run_download_job
from app.jobs.eta import estimate_remaining_seconds
from app.jobs.models import DownloadJob
from app.jobs.store import create_job, delete_result_file, get_job, track_task
from app.services.exports import require_known_format
from app.templating import templates

router = APIRouter(prefix="/titles/{slug_url}/download")


@router.post("")
async def start_download(
    slug_url: str,
    current_user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    fmt: Annotated[str, Form()],
    translation_index: Annotated[int | None, Form()] = None,
) -> RedirectResponse:
    require_known_format(fmt)
    if translation_index is None:
        # PR 205: a translation already saved for this title (default_translation_index,
        # library_entries) is applied automatically, so this doesn't ask again every time.
        # If a chapter added since the default was saved doesn't resolve at that index
        # (more/different branches than it had before), download_title() simply can't
        # resolve *that* chapter and run_download_job() lands on "needs_translation" as
        # usual (see ranobelib.sdk._resolve_bulk_branch_id) - the saved default doesn't
        # silently pick the wrong translation, it just stops covering every chapter.
        entry = await get_entry(conn, current_user.id, slug_url)
        if entry is not None:
            translation_index = entry.default_translation_index
    job = create_job(slug_url, fmt, user_id=current_user.id)
    task = asyncio.create_task(
        run_download_job(job, translation_index=translation_index)
    )
    track_task(job.id, task)
    return RedirectResponse(f"/titles/{slug_url}/download/{job.id}", status_code=303)


@router.post("/{job_id}/retry")
async def retry_download_with_translation(
    slug_url: str,
    job_id: str,
    current_user: Annotated[User, Depends(require_current_user)],
    translation_index: Annotated[int, Form()],
) -> RedirectResponse:
    """The needs_translation retry form on download_status.html (PR 208) - resumes THIS
    job in place rather than start_download() creating a second, separate one: that used
    to leave the original stuck in "needs_translation" forever (nothing ever moved it out
    of that status) while a brand new job actually did the downloading, so "Загрузки →
    Текущие" showed two entries for the same title after every translation choice."""
    job = _get_job_or_404(slug_url, job_id, current_user)
    if job.status != "needs_translation":
        raise HTTPException(status_code=409, detail="Задача не ожидает выбора перевода")
    task = asyncio.create_task(run_download_job(job, translation_index=translation_index))
    track_task(job.id, task)
    return RedirectResponse(f"/titles/{slug_url}/download/{job.id}", status_code=303)


@router.get("/{job_id}")
async def show_download_status(
    request: Request,
    slug_url: str,
    job_id: str,
    current_user: Annotated[User | None, Depends(get_current_user)],
) -> HTMLResponse:
    job = _get_job_or_404(slug_url, job_id, current_user)
    max_branches = max((len(chapter.branches) for chapter in job.ambiguous_chapters), default=0)
    return templates.TemplateResponse(
        request,
        "download_status.html",
        {"slug_url": slug_url, "job": job, "max_branches": max_branches},
    )


@router.get("/{job_id}/status")
async def download_status(
    slug_url: str,
    job_id: str,
    current_user: Annotated[User | None, Depends(get_current_user)],
) -> JSONResponse:
    job = _get_job_or_404(slug_url, job_id, current_user)
    return JSONResponse(_job_status_payload(job))


@router.get("/{job_id}/file")
async def download_result_file(
    slug_url: str,
    job_id: str,
    current_user: Annotated[User | None, Depends(get_current_user)],
) -> FileResponse:
    job = _get_job_or_404(slug_url, job_id, current_user)
    if job.status != "done":
        raise HTTPException(status_code=404, detail="Файл ещё не готов")
    if job.result_path is None:
        # Already picked up once (this route was hit before, which clears result_path -
        # see delete_result_file()) or swept by TTL for sitting unclaimed too long.
        raise HTTPException(status_code=404, detail="Файл уже скачан или срок хранения истёк")
    return FileResponse(
        job.result_path,
        filename=f"{slug_url}.{job.fmt}",
        background=BackgroundTask(delete_result_file, job),
    )


def _get_job_or_404(slug_url: str, job_id: str, current_user: User | None) -> DownloadJob:
    job = get_job(job_id)
    if job is None or job.slug_url != slug_url:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if job.user_id is not None:
        # An anonymous download (job.user_id is None) stays open to anyone who has the
        # job_id, as before - only a job tied to an account is scoped to its owner, defense
        # in depth on top of the job_id's own unguessability (see roadmap PR 190).
        if current_user is None or job.user_id != current_user.id:
            raise HTTPException(
                status_code=403, detail="Эта задача принадлежит другому пользователю"
            )
    return job


def _job_status_payload(job: DownloadJob) -> dict[str, Any]:
    return {
        "status": job.status,
        "completed": job.completed,
        "total": job.total,
        "error": job.error,
        "eta_seconds": estimate_remaining_seconds(job),
    }

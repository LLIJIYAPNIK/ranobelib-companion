"""The "Загрузки" section: the logged-in visitor's own active jobs + finished history."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.auth.dependencies import get_current_user, require_current_user
from app.config import get_settings
from app.db.connection import get_connection
from app.db.downloads import delete_entry, list_download_history
from app.db.users import User
from app.jobs.eta import estimate_remaining_seconds
from app.jobs.models import DownloadJob
from app.jobs.store import (
    list_active_jobs_for_user,
    list_ready_jobs_for_user,
    ready_file_url,
    sweep_expired_result_files,
)
from app.templating import templates

router = APIRouter(prefix="/downloads")


@router.get("")
async def show_downloads(
    request: Request, user: Annotated[User | None, Depends(get_current_user)]
) -> HTMLResponse:
    """Viewing the page itself doesn't require an account - same locked-screen gate as
    /library (PR 22): an anonymous visitor just can't have any downloads of their own, so
    that's the one thing the page won't show them (downloads.html prompts them to log in/
    register instead of the lists). Starting/tracking an actual download still requires
    login wherever it spends ranobelib.me API quota - see require_current_user below and
    on POST /titles/{slug_url}/download."""
    active_jobs = list_active_jobs_for_user(user.id) if user is not None else []
    history = list_download_history(get_connection(), user.id) if user is not None else []
    ready_files = (
        {
            entry.id: url
            for entry in history
            if (url := ready_file_url(entry.job_id, user.id)) is not None
        }
        if user is not None
        else {}
    )
    return templates.TemplateResponse(
        request,
        "downloads.html",
        {
            "active_nav": "downloads",
            "active_jobs": active_jobs,
            "history": history,
            "ready_files": ready_files,
        },
    )


@router.get("/status")
async def list_downloads_status(
    user: Annotated[User, Depends(require_current_user)],
) -> JSONResponse:
    jobs = list_active_jobs_for_user(user.id)
    return JSONResponse([_job_summary(job) for job in jobs])


@router.get("/ready")
async def list_downloads_ready(
    user: Annotated[User, Depends(require_current_user)],
) -> JSONResponse:
    """Polled from every page (see app/static/js/download-ready.js), not just a specific
    job's own status page - so a title downloaded and then left to run in the background
    still gets delivered to the visitor once it's done, wherever they've navigated to."""
    sweep_expired_result_files(get_settings().download_file_ttl)
    jobs = list_ready_jobs_for_user(user.id)
    return JSONResponse([_ready_job_summary(job) for job in jobs])


@router.delete("/history/{entry_id}")
async def delete_download_history_entry(
    entry_id: int,
    user: Annotated[User, Depends(require_current_user)],
) -> Response:
    if not delete_entry(get_connection(), entry_id, user.id):
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return Response(status_code=204)


def _job_summary(job: DownloadJob) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "slug_url": job.slug_url,
        "fmt": job.fmt,
        "status": job.status,
        "completed": job.completed,
        "total": job.total,
        "eta_seconds": estimate_remaining_seconds(job),
    }


def _ready_job_summary(job: DownloadJob) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "slug_url": job.slug_url,
        "fmt": job.fmt,
        "file_url": f"/titles/{job.slug_url}/download/{job.id}/file",
    }

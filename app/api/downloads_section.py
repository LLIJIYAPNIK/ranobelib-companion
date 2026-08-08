"""The "Загрузки" section: the logged-in visitor's own active jobs + finished history."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.auth.dependencies import get_current_user, require_current_user
from app.db.connection import get_connection
from app.db.downloads import list_download_history
from app.db.users import User
from app.jobs.eta import estimate_remaining_seconds
from app.jobs.models import DownloadJob
from app.jobs.store import list_active_jobs_for_user
from app.templating import templates

router = APIRouter(prefix="/downloads")


@router.get("")
async def show_downloads(
    request: Request, user: Annotated[User | None, Depends(get_current_user)]
) -> HTMLResponse:
    """Viewing the page doesn't require an account - same "public page, personal content
    needs a login" split as /library (PR 14)."""
    active_jobs = list_active_jobs_for_user(user.id) if user is not None else []
    history = list_download_history(get_connection(), user.id) if user is not None else []
    return templates.TemplateResponse(
        request,
        "downloads.html",
        {"active_nav": "downloads", "active_jobs": active_jobs, "history": history},
    )


@router.get("/status")
async def list_downloads_status(
    user: Annotated[User, Depends(require_current_user)],
) -> JSONResponse:
    jobs = list_active_jobs_for_user(user.id)
    return JSONResponse([_job_summary(job) for job in jobs])


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

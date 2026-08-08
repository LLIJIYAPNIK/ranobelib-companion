"""The "Загрузки" section: the logged-in visitor's own active jobs + finished history."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.auth.dependencies import require_current_user
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
    request: Request, user: Annotated[User, Depends(require_current_user)]
) -> HTMLResponse:
    """Unlike /library, downloading always required knowing who's asking (it spends
    ranobelib.me API quota) - viewing the section follows the same rule now."""
    active_jobs = list_active_jobs_for_user(user.id)
    history = list_download_history(get_connection(), user.id)
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

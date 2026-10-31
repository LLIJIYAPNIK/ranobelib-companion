"""The "Загрузки" section: the logged-in visitor's own active jobs + finished history."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from psycopg import AsyncConnection

from app.auth.dependencies import get_current_user, require_current_user
from app.config import get_settings
from app.db.connection import connection, get_connection
from app.db.downloads import (
    DownloadHistoryEntry,
    clear_history,
    delete_entry,
    list_download_history,
)
from app.db.library import list_entries
from app.db.users import User
from app.jobs.eta import estimate_remaining_seconds
from app.jobs.models import DownloadJob
from app.jobs.store import (
    list_active_jobs_for_user,
    list_ready_jobs_for_user,
    ready_file_url,
    sweep_expired_result_files,
)
from app.services.exports import available_export_formats
from app.services.titles import TitleSummary, title_summaries
from app.templating import templates
from app.timezones import local_today, user_timezone

# «Скачать тайтл»: how many titles from the library are offered for a one-click pick.
QUICK_PICKS = 6

_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)

router = APIRouter(prefix="/downloads")


@dataclass(frozen=True)
class DownloadHistoryGroup:
    date: str
    label: str
    entries: list[DownloadHistoryEntry]


@dataclass(frozen=True)
class DownloadHistorySummary:
    """PR 314: the «Сводка» card - counted from the history the page already loaded
    (list_download_history()'s most recent entries), nothing fetched or estimated on top."""

    total: int
    done: int
    errors: int
    cancelled: int
    last: DownloadHistoryEntry | None
    # Chapters in the files that came out - the hero's «N глав».
    chapters: int = 0


@dataclass(frozen=True)
class TranslationVariant:
    """One «Вариант N» of a job waiting for a translation (needs_translation): the
    translation_index it stands for, who is behind it in most of the chapters that need
    the choice, and in how many of them it exists at all."""

    index: int
    who: str
    chapters: int


@router.get("")
async def show_downloads(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)],
) -> HTMLResponse:
    """Viewing the page itself doesn't require an account - same locked-screen gate as
    /library (PR 22): an anonymous visitor just can't have any downloads of their own, so
    that's the one thing the page won't show them (downloads.html prompts them to log in/
    register instead of the lists). Starting/tracking an actual download still requires
    login wherever it spends ranobelib.me API quota - see require_current_user below and
    on POST /titles/{slug_url}/download. conn is checked out below, not taken as a
    route-level Depends(get_connection) parameter, so an anonymous visitor never checks
    one out of the pool at all (see get_current_user()'s own docstring for the same
    reasoning)."""
    active_jobs = []
    history: list[DownloadHistoryEntry] = []
    ready_files: dict[int, str] = {}
    titles: dict[str, TitleSummary] = {}
    picks: list[str] = []
    tz = user_timezone(user.timezone if user is not None else None)
    if user is not None:
        active_jobs = list_active_jobs_for_user(user.id)
        async with connection() as conn:
            history = await list_download_history(conn, user.id)
            # «Скачать тайтл» offers the library's latest reads for a one-click pick.
            picks = [entry.slug_url for entry in (await list_entries(conn, user.id))[:QUICK_PICKS]]
        ready_files = {
            entry.id: url
            for entry in history
            if (url := ready_file_url(entry.job_id, user.id)) is not None
        }
        # PR 281: the title's name and cover on each history card, the running jobs and
        # the picks; a title the SDK can't resolve stays out, and its card leads with the
        # file name instead.
        slugs = {entry.slug_url for entry in history} | {job.slug_url for job in active_jobs}
        titles = await title_summaries(slugs | set(picks))
    return templates.TemplateResponse(
        request,
        "downloads.html",
        {
            "active_nav": "downloads",
            "active_jobs": active_jobs,
            "history": history,
            "history_groups": _group_history(history, tz),
            "history_summary": _summarize_history(history),
            "history_times": {entry.id: _local_time(entry.finished_at, tz) for entry in history},
            "ready_files": ready_files,
            "history_titles": titles,
            # A job waiting for a translation is answered right in its card.
            "translation_variants": {
                job.id: _translation_variants(job)
                for job in active_jobs
                if job.status == "needs_translation"
            },
            "quick_picks": [slug for slug in picks if slug in titles],
            "export_formats": available_export_formats(),
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
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> Response:
    if not await delete_entry(conn, entry_id, user.id):
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return Response(status_code=204)


@router.delete("/history")
async def clear_download_history(
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> Response:
    await clear_history(conn, user.id)
    return Response(status_code=204)


def _local(timestamp: str, tz: str) -> datetime | None:
    """finished_at (UTC ISO-8601) on the user's own clock."""
    try:
        return datetime.fromisoformat(timestamp).astimezone(ZoneInfo(tz))
    except ValueError:
        return None


def _local_time(timestamp: str, tz: str) -> str:
    local = _local(timestamp, tz)
    return f"{local:%H:%M}" if local else timestamp[11:16]


def _day_label(day: date, today: date) -> str:
    if day == today:
        return "Сегодня"
    if day == today - timedelta(days=1):
        return "Вчера"
    label = f"{day.day} {_MONTHS[day.month - 1]}"
    return label if day.year == today.year else f"{label} {day.year}"


def _group_history(
    entries: list[DownloadHistoryEntry], tz: str = "UTC"
) -> list[DownloadHistoryGroup]:
    """Newest first, one group per day of the user's own calendar (users.timezone, PR
    322): «Сегодня», «Вчера», then «28 октября» (with the year once it isn't this one)."""
    today = local_today(tz)
    groups: list[DownloadHistoryGroup] = []
    for entry in entries:
        local = _local(entry.finished_at, tz)
        day = local.date().isoformat() if local else entry.finished_at[:10]
        if groups and groups[-1].date == day:
            groups[-1].entries.append(entry)
            continue
        label = _day_label(local.date(), today) if local else day
        groups.append(DownloadHistoryGroup(day, label, [entry]))
    return groups


def _branch_label(branch: Any) -> str:
    teams = [team.name for team in (getattr(branch, "teams", None) or [])]
    if teams:
        return ", ".join(teams)
    user = getattr(branch, "user", None)
    return getattr(user, "username", "") or ""


def _translation_variants(job: DownloadJob) -> list[TranslationVariant]:
    """«Вариант N» is a position in each chapter's own list of translations (the SDK's
    translation_index), so the same number can be different teams in different chapters;
    each variant names the team that holds that position in most of them."""
    width = max((len(chapter.branches) for chapter in job.ambiguous_chapters), default=0)
    variants = []
    for index in range(width):
        names = Counter(
            _branch_label(chapter.branches[index])
            for chapter in job.ambiguous_chapters
            if len(chapter.branches) > index
        )
        who = names.most_common(1)[0][0] if names else ""
        variants.append(TranslationVariant(index, who, sum(names.values())))
    return variants


def _summarize_history(entries: list[DownloadHistoryEntry]) -> DownloadHistorySummary:
    def count(status: str) -> int:
        return sum(1 for entry in entries if entry.status == status)

    return DownloadHistorySummary(
        total=len(entries),
        done=count("done"),
        errors=count("error"),
        cancelled=count("cancelled"),
        last=entries[0] if entries else None,
        chapters=sum(entry.chapter_count or 0 for entry in entries if entry.status == "done"),
    )


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

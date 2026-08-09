"""The "Активность" section: today's reading/download activity for the logged-in user."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Form
from fastapi.responses import Response
from ranobelib import RanobeLibError

from app.auth.dependencies import require_current_user
from app.db.activity import list_chapters_read_today, record_heartbeat, total_active_seconds_today
from app.db.connection import get_connection
from app.db.downloads import DownloadHistoryEntry, list_download_history_today
from app.db.users import User
from app.jobs.models import DownloadJob
from app.jobs.store import list_active_jobs_for_user
from app.services.client import open_client

router = APIRouter(prefix="/activity")

_MAX_HEARTBEAT_SECONDS = 60
"""Clamp on a single tick, matching activity-heartbeat.js's own interval (see
app/static/js/activity-heartbeat.js) - keeps a single request from inflating a user's own
active-time stat past what one real interval could ever produce."""


@dataclass(frozen=True)
class ReadToday:
    slug_url: str
    chapters_read: int
    name: str | None
    cover_url: str | None


@dataclass(frozen=True)
class ActivitySummary:
    read_today: list[ReadToday]
    chapters_read_today: int
    active_time_label: str
    active_jobs: list[DownloadJob]
    downloads_today: list[DownloadHistoryEntry]


async def build_activity_summary(user: User) -> ActivitySummary:
    """Everything the "Активность" page (see the upcoming GET /activity route) shows for
    today - "today" meaning the UTC calendar date, matching app/db/activity.py."""
    conn = get_connection()
    read_today = await _read_today_items(user)
    return ActivitySummary(
        read_today=read_today,
        chapters_read_today=sum(item.chapters_read for item in read_today),
        active_time_label=_format_active_time(total_active_seconds_today(conn, user.id)),
        active_jobs=list_active_jobs_for_user(user.id),
        downloads_today=list_download_history_today(conn, user.id),
    )


@router.post("/heartbeat", status_code=204)
async def heartbeat(
    user: Annotated[User, Depends(require_current_user)],
    slug_url: Annotated[str, Form()],
    seconds: Annotated[int, Form(gt=0, le=_MAX_HEARTBEAT_SECONDS)],
) -> Response:
    record_heartbeat(get_connection(), user.id, slug_url, seconds)
    return Response(status_code=204)


async def _read_today_items(user: User) -> list[ReadToday]:
    """Each read-today title's display name/cover, fetched fresh through the SDK - same
    approach as `_library_items()` in app/api/library.py, for the same reason (avoid a
    second cache of SDK response data, see CLAUDE.md, "Архитектура"). A title that's
    gone/unreachable on ranobelib.me still shows up, just with its slug_url as a fallback
    label and no cover.
    """
    items: list[ReadToday] = []
    for count in list_chapters_read_today(get_connection(), user.id):
        name: str | None = None
        cover_url: str | None = None
        try:
            async with open_client(count.slug_url) as lib:
                title = await lib.get_info()
            name = title.name
            cover_url = title.cover.default or title.cover.md or title.cover.thumbnail
        except RanobeLibError:
            pass
        items.append(
            ReadToday(
                slug_url=count.slug_url,
                chapters_read=count.chapters_read,
                name=name,
                cover_url=cover_url,
            )
        )
    return items


def _format_active_time(seconds: int) -> str:
    minutes = seconds // 60
    if minutes == 0:
        return "< 1 мин"
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"

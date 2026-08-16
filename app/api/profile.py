"""GET /profile — read-only account profile (PR 92), now a public page (PR 122).

Separate from /settings/account (PR 90), which is where these same fields are actually
edited - this page only displays them (PR 124's three show_* privacy flags included).

/profile (no id) is the logged-in visitor's own shortcut - it stays gated behind login,
since there's no "own profile" to show an anonymous visitor. /profile/{user_id} is the
public page itself: any visitor, logged in or not, can view any registered user's
profile by id - subject to that user's own show_currently_reading/show_favorite/
show_library flags (PR 124), which only apply to *other* visitors; the owner's own view
of their own profile always shows everything, since that's also where they'd notice
something is set to hidden and go fix it in /settings/account.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.api.library import library_items_for_user
from app.auth.dependencies import get_current_user
from app.db.activity import daily_active_seconds, daily_reading_activity
from app.db.comments import count_comments_by_user
from app.db.connection import get_connection
from app.db.users import User, get_user_by_id
from app.templating import templates

router = APIRouter()

_CALENDAR_WEEKS = 52


@dataclass(frozen=True)
class CalendarDay:
    """One cell of the reading-activity heatmap (PR 136)."""

    count: int
    level: int  # 0 (no activity) - 4 (this user's own busiest day in the window)
    label: str  # tooltip text: exact date + chapter count


@router.get("/profile")
async def own_profile_page(
    request: Request, user: Annotated[User | None, Depends(get_current_user)]
) -> HTMLResponse:
    """Same locked-screen gate as /library, /downloads, /activity and /settings/*
    (PR 22/90/91) - there's nothing to redirect an anonymous visitor's *own* profile to."""
    if user is None:
        return templates.TemplateResponse(request, "profile.html", {"profile_user": None})
    return await _render_profile(request, profile_user=user, is_own_profile=True)


@router.get("/profile/{user_id}")
async def public_profile_page(
    request: Request,
    user_id: int,
    viewer: Annotated[User | None, Depends(get_current_user)],
) -> HTMLResponse:
    profile_user = get_user_by_id(get_connection(), user_id)
    if profile_user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    is_own_profile = viewer is not None and viewer.id == profile_user.id
    return await _render_profile(request, profile_user=profile_user, is_own_profile=is_own_profile)


async def _render_profile(
    request: Request, *, profile_user: User, is_own_profile: bool
) -> HTMLResponse:
    items = await library_items_for_user(profile_user)
    # Most recently read first (see library_items_for_user/list_entries's own ordering) -
    # but the top entry might just be the most recently *added*, never actually opened,
    # so "Читает сейчас" only shows up once that entry genuinely has a read position.
    currently_reading = items[0] if items and items[0]["entry"].last_read_at else None
    favorite_item = next((item for item in items if item["entry"].is_favorite), None)

    # PR 124: hide whichever sections the profile owner has opted out of, but only from
    # someone else's view - computed above unconditionally so the owner's own visit is
    # completely unaffected by their own flags.
    if not is_own_profile:
        if not profile_user.show_currently_reading:
            currently_reading = None
        if not profile_user.show_favorite:
            favorite_item = None
        if not profile_user.show_library:
            items = []

    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "profile_user": profile_user,
            "is_own_profile": is_own_profile,
            "registered_at": _format_date(profile_user.created_at),
            # PR 135/136: unlike currently_reading/favorite_item/library_items above, not
            # gated by any show_* privacy flag - there isn't one for either, same as the
            # avatar/nickname/bio they sit alongside.
            "comment_count": count_comments_by_user(get_connection(), profile_user.id),
            "reading_calendar": _build_reading_calendar(profile_user.id),
            "currently_reading": currently_reading,
            "favorite_item": favorite_item,
            "library_items": items,
        },
    )


def _format_date(iso_timestamp: str) -> str:
    return datetime.fromisoformat(iso_timestamp).strftime("%d.%m.%Y")


def _build_reading_calendar(user_id: int) -> list[CalendarDay]:
    """Every day in the trailing _CALENDAR_WEEKS weeks, oldest first, padded back to the
    most recent Sunday on/before the window's own start so the flat list can be dropped
    straight into a `grid-auto-flow: column; grid-template-rows: repeat(7, ...)` grid
    (app.css's .reading-calendar) and land each day in the correct weekday row - the same
    "whole Sunday-to-Saturday weeks, partial leading week zero-filled" alignment GitHub's
    own contribution graph uses. A day with no chapter_read events at all (including every
    padding day, which by construction predates anything daily_reading_activity() even
    queried for) gets level 0, same as a real day with zero chapters read - there's no
    distinct "no data" state, an empty calendar for a user with no reading history at all
    just means every cell is level 0, not an empty/missing grid."""
    conn = get_connection()
    counts = daily_reading_activity(conn, user_id, weeks=_CALENDAR_WEEKS)
    active_seconds = daily_active_seconds(conn, user_id, weeks=_CALENDAR_WEEKS)
    max_count = max(counts.values(), default=0)

    today = datetime.now(UTC).date()
    start = today - timedelta(days=_CALENDAR_WEEKS * 7 - 1)
    # date.weekday() is Monday=0..Sunday=6, so days-since-the-most-recent-Sunday is
    # (weekday + 1) % 7.
    grid_start = start - timedelta(days=(start.weekday() + 1) % 7)

    days: list[CalendarDay] = []
    current = grid_start
    while current <= today:
        day_key = current.isoformat()
        count = counts.get(day_key, 0)
        seconds = active_seconds.get(day_key, 0)
        level = 0 if max_count == 0 or count == 0 else max(1, round(count / max_count * 4))
        days.append(
            CalendarDay(
                count=count,
                level=level,
                # PR 140: the chapter count alone doesn't say how long that reading
                # actually took - _format_duration() reuses the same heartbeat seconds
                # already summed for "Активность"'s "today" stat (total_active_seconds_
                # today()), just grouped by day instead of collapsed to one number.
                label=(
                    f"{current.strftime('%d.%m.%Y')}: {_pluralize_chapters(count)}, "
                    f"{_format_duration(seconds)}"
                ),
            )
        )
        current += timedelta(days=1)
    return days


def _pluralize_chapters(n: int) -> str:
    if n == 0:
        return "нет прочитанных глав"
    mod10, mod100 = n % 10, n % 100
    if mod10 == 1 and mod100 != 11:
        word = "глава"
    elif 2 <= mod10 <= 4 and not (12 <= mod100 <= 14):
        word = "главы"
    else:
        word = "глав"
    return f"{n} {word}"


def _format_duration(seconds: int) -> str:
    """Renders as e.g. '2 ч 15 мин' once there's at least an hour, otherwise just
    '45 мин' - no leading '0 ч', and '0 мин' (not blank/omitted) for a day with chapters
    read but no heartbeat ticks, e.g. every chapter was opened and closed faster than a
    single heartbeat tick (see app/api/activity.py)."""
    minutes = seconds // 60
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"

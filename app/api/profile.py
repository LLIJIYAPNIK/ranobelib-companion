"""GET /profile — read-only account profile (PR 92), now a public page (PR 122).

Separate from /settings/account (PR 90), which is where these same fields are actually
edited - this page only displays them (PR 124's show_* privacy flags included).

/profile (no id) is the logged-in visitor's own shortcut - it stays gated behind login,
since there's no "own profile" to show an anonymous visitor. /profile/{user_id} is the
public page itself: any visitor, logged in or not, can view any registered user's
profile by id - subject to that user's own show_currently_reading/show_library flags
(PR 124), which only apply to *other* visitors; the owner's own view
of their own profile always shows everything, since that's also where they'd notice
something is set to hidden and go fix it in /settings/account.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from psycopg import AsyncConnection

from app.api.activity import (
    DAY_PATTERN,
    MONTH_PATTERN,
    WEEKDAYS,
    build_reading_month,
    parse_calendar_query,
)
from app.api.library import currently_reading_for_users, library_items_for_user
from app.auth.dependencies import get_current_user
from app.db.activity import daily_active_seconds
from app.db.comments import count_comments_by_user
from app.db.connection import connection, get_connection
from app.db.friendships import get_friend_button_state, list_friends
from app.db.users import User, get_user_by_id
from app.templating import templates
from app.timezones import user_timezone

router = APIRouter()

# The «за год» figure: reading time over the trailing year.
_YEAR_WEEKS = 52


# PR 201: how many friends the profile page's own "Друзья" section previews before
# pointing at the full list (GET /profile/{user_id}/friends) instead of listing everyone
# right there.
_FRIEND_PREVIEW_LIMIT = 6

# PR 277: the "Библиотека" block is a preview of covers, with "Открыть библиотеку" for
# the rest - not every title in the library. Two rows of five, beside "Друзья".
_LIBRARY_PREVIEW_LIMIT = 10


@dataclass(frozen=True)
class ProfileStat:
    """One of the hero's figures: a big value over a small label."""

    value: str
    label: str


@router.get("/profile")
async def own_profile_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)],
    month: Annotated[str | None, Query(pattern=MONTH_PATTERN)] = None,
    day: Annotated[str | None, Query(pattern=DAY_PATTERN)] = None,
) -> HTMLResponse:
    """Same locked-screen gate as /library, /downloads, /activity and /settings/*
    (PR 22/90/91) - there's nothing to redirect an anonymous visitor's *own* profile to.
    conn is checked out below, not taken as a route-level Depends(get_connection)
    parameter, so an anonymous visitor - the branch right below - never checks one out of
    the pool at all (see get_current_user()'s own docstring for the same reasoning)."""
    if user is None:
        return templates.TemplateResponse(request, "profile.html", {"profile_user": None})
    async with connection() as conn:
        return await _render_profile(
            request,
            profile_user=user,
            is_own_profile=True,
            viewer_id=user.id,
            conn=conn,
            calendar_query=parse_calendar_query(month, day),
        )


@router.get("/profile/{user_id}")
async def public_profile_page(
    request: Request,
    user_id: int,
    viewer: Annotated[User | None, Depends(get_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    month: Annotated[str | None, Query(pattern=MONTH_PATTERN)] = None,
    day: Annotated[str | None, Query(pattern=DAY_PATTERN)] = None,
) -> HTMLResponse:
    """`month`/`day` pick the reading calendar's month and day, as on «Активность»."""
    profile_user = await get_user_by_id(conn, user_id)
    if profile_user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    is_own_profile = viewer is not None and viewer.id == profile_user.id
    return await _render_profile(
        request,
        profile_user=profile_user,
        is_own_profile=is_own_profile,
        viewer_id=viewer.id if viewer is not None else None,
        conn=conn,
        calendar_query=parse_calendar_query(month, day),
    )


async def _render_profile(
    request: Request,
    *,
    profile_user: User,
    is_own_profile: bool,
    viewer_id: int | None,
    conn: AsyncConnection,
    calendar_query: tuple[date | None, date | None] = (None, None),
) -> HTMLResponse:
    items = await library_items_for_user(profile_user, conn)
    # Most recently read first (see library_items_for_user/list_entries's own ordering) -
    # but the top entry might just be the most recently *added*, never actually opened,
    # so "Читает сейчас" only shows up once that entry genuinely has a read position.
    currently_reading = items[0] if items and items[0]["entry"].last_read_at else None

    # PR 201/202: the friends preview shown further down, gated the same way as the three
    # sections right below (computed unconditionally so the owner's own visit is
    # unaffected either way).
    friends = await list_friends(conn, profile_user.id)

    # PR 124/202: hide whichever sections the profile owner has opted out of, but only
    # from someone else's view - computed above unconditionally so the owner's own visit
    # is completely unaffected by their own flags.
    if not is_own_profile:
        if not profile_user.show_currently_reading:
            currently_reading = None
        if not profile_user.show_library:
            items = []
        if not profile_user.show_friends:
            friends = []

    # PR 199: which of the "Добавить в друзья" button's states to show - only meaningful
    # for a logged-in visitor looking at someone else's profile; None otherwise (an
    # anonymous visitor, or your own profile, has no friend-request UI at all).
    friend_state = None
    if viewer_id is not None and not is_own_profile:
        friend_state = await get_friend_button_state(conn, viewer_id, profile_user.id)

    friend_preview = friends[:_FRIEND_PREVIEW_LIMIT]
    comment_count = await count_comments_by_user(
        conn, profile_user.id, include_hidden=is_own_profile
    )
    # PR 322: the profile owner's own days, on their own clock, whoever is looking at
    # them - the month calendar of «Активность» (without today's «Читать дальше»: the
    # hero has it).
    month, day = calendar_query
    reading_month = await build_reading_month(profile_user, conn, month, day)
    year_seconds = await daily_active_seconds(
        conn, profile_user.id, weeks=_YEAR_WEEKS, tz=user_timezone(profile_user.timezone)
    )
    # PR 277: a hidden section counts as empty here too - the same neutral "0" a genuinely
    # empty library/friend list shows, never a different state that would reveal a flag.
    stats = [
        ProfileStat(_format_duration(sum(year_seconds.values())), "за год"),
        ProfileStat(str(len(items)), _plural(len(items), "тайтл", "тайтла", "тайтлов")),
        ProfileStat(str(len(friends)), _plural(len(friends), "друг", "друга", "друзей")),
        ProfileStat(
            str(comment_count),
            _plural(comment_count, "комментарий", "комментария", "комментариев"),
        ),
    ]

    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "profile_user": profile_user,
            "is_own_profile": is_own_profile,
            "friend_state": friend_state,
            "registered_at": _format_date(profile_user.created_at),
            # PR 135/136: unlike currently_reading/library_items above, not
            # gated by any show_* privacy flag - there isn't one for either, same as the
            # avatar/nickname/bio they sit alongside.
            "comment_count": comment_count,
            "reading": reading_month,
            "weekdays": WEEKDAYS,
            "calendar_base": "/profile" if is_own_profile else f"/profile/{profile_user.id}",
            "currently_reading": currently_reading,
            "library_items": items,
            "library_preview": items[:_LIBRARY_PREVIEW_LIMIT],
            "friend_preview": friend_preview,
            "friend_count": len(friends),
            "friend_reading": await _friends_reading(friend_preview, conn),
            "stats": stats,
        },
    )


@router.get("/profile/{user_id}/friends")
async def profile_friends_page(
    request: Request,
    user_id: int,
    viewer: Annotated[User | None, Depends(get_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> HTMLResponse:
    """The full list behind the profile page's own "Друзья" preview/"Показать всех" link
    (PR 201) - public the same way /profile/{user_id} itself is, unlike /friends (no id),
    which only ever shows the logged-in visitor's *own* requests/friends and has no notion
    of "whose list is this". Just the accepted-friends list, read-only - no request
    management here, that stays on /friends.

    PR 202: this URL is just profile_user.id + "/friends" - trivially guessable, so
    show_friends has to gate this page too, not only its own preview on profile.html, or
    the toggle would do nothing for anyone who finds/remembers the link. Same neutral
    "just show the empty state" treatment the rest of this app gives a privacy-hidden
    section (see _render_profile) - never a distinct "this is hidden" message that would
    itself leak whether a flag is off."""
    profile_user = await get_user_by_id(conn, user_id)
    if profile_user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    is_own_profile = viewer is not None and viewer.id == profile_user.id
    friends = []
    if is_own_profile or profile_user.show_friends:
        friends = await list_friends(conn, profile_user.id)
    return templates.TemplateResponse(
        request, "profile_friends.html", {"profile_user": profile_user, "friends": friends}
    )


async def _friends_reading(friends: list, conn: AsyncConnection) -> dict[int, str]:
    """PR 277: the "Читает «…»" line under each friend in the profile's "Друзья" preview,
    keyed by user id - the same lookup and the same show_currently_reading rule as the home
    page's friend activity (PR 200), for at most _FRIEND_PREVIEW_LIMIT friends. A friend
    with nothing to show is simply absent."""
    if not friends:
        return {}
    by_user = await currently_reading_for_users([f.user.id for f in friends], conn)
    reading: dict[int, str] = {}
    for user_id, item in by_user.items():
        friend_user = await get_user_by_id(conn, user_id)
        if friend_user is None or not friend_user.show_currently_reading:
            continue
        reading[user_id] = item["name"] or item["entry"].slug_url  # type: ignore[union-attr]
    return reading


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many


def _format_date(iso_timestamp: str) -> str:
    return datetime.fromisoformat(iso_timestamp).strftime("%d.%m.%Y")


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

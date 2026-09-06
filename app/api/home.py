"""GET / — the search/open-title landing page."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from ranobelib import RanobeLibError

from app.api.library import currently_reading_for_users
from app.auth.dependencies import get_current_user
from app.db.activity import daily_reading_activity, reading_streak_days
from app.db.comments import RecentComment, list_recent_comments_by_user
from app.db.connection import connection
from app.db.friendships import FriendUser, list_friends
from app.db.library import get_entry
from app.db.users import User, get_user_by_id
from app.reading_progress import reading_progress_percent
from app.recent_titles import forget, read_recent
from app.services.client import open_client
from app.templating import templates

router = APIRouter()


@dataclass(frozen=True)
class FriendActivityCard:
    """One friend's tile in PR 200's home-page "Активность друзей" column - what they're
    currently reading (subject to their own show_currently_reading, PR 90/124), their most
    recent comments, and their current reading streak."""

    friend: FriendUser
    currently_reading: dict[str, object] | None
    recent_comments: list[RecentComment]
    streak_days: int

    @property
    def streak_label(self) -> str | None:
        if self.streak_days <= 0:
            return None
        return f"{self.streak_days} {_pluralize_days(self.streak_days)} подряд"

    @property
    def has_activity(self) -> bool:
        return bool(self.currently_reading or self.recent_comments or self.streak_days > 0)


@router.get("/")
async def home(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "active_nav": "home",
            "recent": await _recent_with_progress(request, user),
            "friend_activity": await _friend_activity_cards(user),
        },
    )


@router.post("/recent/{slug_url}/forget", response_model=None)
async def forget_recent_title(request: Request, slug_url: str) -> Response:
    """The "×" on a "Недавние" card (PR 69) - rewrites the cookie without this entry and
    returns 204 so recent-titles-forget.js can just drop the card from the page, no
    reload needed."""
    response = Response(status_code=204)
    forget(response, request, slug_url=slug_url)
    return response


async def _recent_with_progress(
    request: Request, user: User | None
) -> list[dict[str, str | int | None]]:
    """"Недавние" entries, each with a `progress_percent` alongside the usual `slug_url`/
    `name`/`cover_url` (see recent_titles.read_recent) - PR 68.

    Only computed for a logged-in user whose personal library (PR 14) has a matching
    `LibraryEntry` with recorded progress: an anonymous visitor has no `LibraryEntry` to
    match against at all, and a title only ever opened from the description page (never
    read) has no recorded position either. Neither case is a reason to start a parallel
    progress store on top of the one PR 27 already reads from `db/library.py`. conn is
    checked out here, not taken as a route-level Depends(get_connection) parameter, so an
    anonymous request never checks one out of the pool at all (see get_current_user()'s
    own docstring for the same reasoning).
    """
    recent = read_recent(request)
    if user is None:
        return [dict(item, progress_percent=None) for item in recent]
    result: list[dict[str, str | int | None]] = []
    async with connection() as conn:
        for item in recent:
            progress_percent = None
            entry = await get_entry(conn, user.id, item["slug_url"])
            if entry is not None and entry.last_read_volume is not None:
                try:
                    async with open_client(item["slug_url"]) as lib:
                        volumes = await lib.get_table_of_contents()
                    progress_percent = reading_progress_percent(
                        volumes, entry.last_read_volume, entry.last_read_number
                    )
                except RanobeLibError:
                    pass
            result.append(dict(item, progress_percent=progress_percent))
    return result


async def _friend_activity_cards(user: User | None) -> list[FriendActivityCard]:
    """PR 200's right-hand "Активность друзей" column - empty for an anonymous visitor (no
    account, so no friends list), a user with no friends yet, or a user who's turned the
    column off for themselves (PR 202's show_friends_activity_home - a personal preference
    about this user's own home page, unlike the other four privacy flags this one has no
    "someone else's view" to ignore it on), in which case index.html renders no column at
    all rather than an empty shell."""
    if user is None or not user.show_friends_activity_home:
        return []
    async with connection() as conn:
        friends = await list_friends(conn, user.id)
        if not friends:
            return []
        friend_ids = [entry.user.id for entry in friends]
        currently_reading_by_user = await currently_reading_for_users(friend_ids, conn)
        cards: list[FriendActivityCard] = []
        for entry in friends:
            friend_user = await get_user_by_id(conn, entry.user.id)
            currently_reading = currently_reading_by_user.get(entry.user.id)
            # PR 90/124's "Читает сейчас" privacy flag - what a friend has opted to hide
            # from *other* visitors on their own profile stays hidden here too, for the
            # same reason (see app/api/profile.py's own use of this flag).
            if (
                currently_reading is not None
                and friend_user is not None
                and not friend_user.show_currently_reading
            ):
                currently_reading = None
            recent_comments = await list_recent_comments_by_user(conn, entry.user.id)
            streak_days = reading_streak_days(await daily_reading_activity(conn, entry.user.id))
            cards.append(
                FriendActivityCard(
                    friend=entry.user,
                    currently_reading=currently_reading,
                    recent_comments=recent_comments,
                    streak_days=streak_days,
                )
            )
        return cards


def _pluralize_days(n: int) -> str:
    mod10, mod100 = n % 10, n % 100
    if mod10 == 1 and mod100 != 11:
        return "день"
    if 2 <= mod10 <= 4 and not (12 <= mod100 <= 14):
        return "дня"
    return "дней"

"""Friend requests and relationships (PR 199) - the /friends list page plus the three
POST actions both that page and the "Добавить в друзья" button on profile.html
(app/api/profile.py) post to. The button's own state (which of the three actions to even
show) is computed in profile.py, not here - this module only owns the actions themselves
and the page that lists them all together.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from psycopg import AsyncConnection

from app.auth.dependencies import get_current_user, require_current_user
from app.db.connection import connection, get_connection
from app.db.friendships import (
    FriendEntry,
    FriendRequestEntry,
    FriendSearchResult,
    accept_request,
    friend_user_from_user,
    get_friend_button_state,
    list_friends,
    list_incoming_requests,
    list_outgoing_requests,
    remove_relationship,
    send_request,
)
from app.db.notifications import notify_friend_accept, notify_friend_request
from app.db.users import User, get_user_by_id, search_users_by_nickname
from app.templating import templates

router = APIRouter(prefix="/friends")


@router.get("")
async def show_friends(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)],
    query: str | None = None,
) -> HTMLResponse:
    """Same locked-screen gate as /library, /downloads, /activity (PR 22) - viewing the
    page itself doesn't require an account. conn is checked out below, not taken as a
    route-level Depends(get_connection) parameter, so an anonymous visitor never checks
    one out of the pool at all (see get_current_user()'s own docstring for the same
    reasoning).

    `query` (PR 209): the page's own "find someone to add" search - a plain GET query
    param rather than a separate route/page, same shape as the catalog's own `query`
    (app/api/library.py). Omitted or blank shows no search-results section at all, not an
    empty-state message for a search nobody actually ran."""
    incoming: list[FriendRequestEntry] = []
    outgoing: list[FriendRequestEntry] = []
    friends: list[FriendEntry] = []
    search_results: list[FriendSearchResult] = []
    if user is not None:
        async with connection() as conn:
            incoming = await list_incoming_requests(conn, user.id)
            outgoing = await list_outgoing_requests(conn, user.id)
            friends = await list_friends(conn, user.id)
            if query:
                search_results = await _search_friends(conn, user.id, query)
    return templates.TemplateResponse(
        request,
        "friends.html",
        {
            "active_nav": "friends",
            "incoming_requests": incoming,
            "outgoing_requests": outgoing,
            "friends": friends,
            "query": query,
            "search_results": search_results,
        },
    )


async def _search_friends(
    conn: AsyncConnection, viewer_id: int, query: str
) -> list[FriendSearchResult]:
    """`query`'s search results, each paired with `viewer_id`'s own friend_request_actions
    state against them - excludes `viewer_id` itself, since suggesting "add yourself" makes
    no sense regardless of what search_users_by_nickname() itself happens to match."""
    found_users = await search_users_by_nickname(conn, query)
    return [
        FriendSearchResult(
            user=friend_user_from_user(found_user),
            state=await get_friend_button_state(conn, viewer_id, found_user.id),
        )
        for found_user in found_users
        if found_user.id != viewer_id
    ]


@router.post("/{other_user_id}/request")
async def send_friend_request(
    other_user_id: int,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    if other_user_id == user.id:
        raise HTTPException(status_code=400, detail="Нельзя добавить самого себя в друзья")
    if await get_user_by_id(conn, other_user_id) is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    friendship, created = await send_request(conn, user.id, other_user_id)
    if created:
        await notify_friend_request(conn, recipient_id=other_user_id, actor_user_id=user.id)
    return RedirectResponse(url=_safe_next(next, f"/profile/{other_user_id}"), status_code=303)


@router.post("/{other_user_id}/accept")
async def accept_friend_request(
    other_user_id: int,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """`other_user_id` is the requester here - `user` (the logged-in visitor) is always
    the addressee accepting, regardless of whether this was posted from /friends or from
    the requester's own profile page."""
    accepted = await accept_request(conn, user.id, requester_id=other_user_id)
    if accepted:
        await notify_friend_accept(conn, recipient_id=other_user_id, actor_user_id=user.id)
    return RedirectResponse(url=_safe_next(next, "/friends"), status_code=303)


@router.post("/{other_user_id}/remove")
async def remove_friend_relationship(
    other_user_id: int,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Covers three UI actions at once - declining an incoming request, cancelling an
    outgoing one, and unfriending an accepted one - since all three are the same "this
    relationship no longer exists" DELETE (see `friendships.remove_relationship()`'s own
    docstring for why they share one function)."""
    await remove_relationship(conn, user.id, other_user_id)
    return RedirectResponse(url=_safe_next(next, "/friends"), status_code=303)


def _safe_next(next_url: str | None, default: str) -> str:
    """Only a same-site path is accepted as a redirect target - `next` comes straight
    from the request body, so anything else (an absolute URL, `//evil.example`) would be
    an open redirect. Same guard as app/api/library.py's own `_safe_next()`."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return default

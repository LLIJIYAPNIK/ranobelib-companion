"""Friend requests and relationships (PR 199) - the three POST actions the "Добавить в
друзья" button on profile.html (app/api/profile.py) posts to. The button's own state
(which of the three actions to even show) is computed in profile.py, not here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from psycopg import AsyncConnection

from app.auth.dependencies import require_current_user
from app.db.connection import get_connection
from app.db.friendships import accept_request, remove_relationship, send_request
from app.db.notifications import notify_friend_accept, notify_friend_request
from app.db.users import User, get_user_by_id

router = APIRouter(prefix="/friends")


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

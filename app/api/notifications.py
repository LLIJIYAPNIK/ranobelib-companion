"""PR 168 added the sidebar bell's own two JSON endpoints - a lightweight unread count
polled on every page (same idea as app/api/downloads_section.py's /downloads/status) and
the recent-list fetched once the panel itself opens. PR 169 adds the "Все уведомления"
page (GET "") and its own infinite-scroll fragment (GET /page) on top of the same
app/db/notifications.py - same server-renders-the-cards, no-client-templating shape as
app/api/library.py's catalog/catalog_page_fragment (app/static/js/catalog-scroll.js), and
reusing the exact same card markup as the bell panel (app/templates/_notification_card.html).
PR 170 adds mark-read/delete (POST .../read, DELETE ...) - both return the fresh
unread_count in the response body so notifications-actions.js can update the bell's badge
without a page reload.

PR 311 (Webnovells): the bell panel stops building its cards in JS - GET /panel returns
the same _notification_card.html macro's markup as the page, so both surfaces share one
card implementation instead of a Jinja one and a renderNotification() kept in sync by
hand. The page groups its cards as «Новые»/«Ранее» (_group() below).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from psycopg import AsyncConnection

from app.auth.dependencies import require_current_user
from app.db.connection import get_connection
from app.db.notifications import (
    Notification,
    count_unread_notifications,
    delete_notification,
    list_notifications_page,
    list_recent_notifications,
    mark_notification_read,
)
from app.db.users import User
from app.templating import templates

router = APIRouter(prefix="/notifications")

# How many notifications the panel shows - the "Все уведомления" page below is where
# seeing further back belongs, not a longer list crammed into this same small panel.
RECENT_LIMIT = 20

# One page of the "Все уведомления" list - deliberately more than RECENT_LIMIT (this is
# the page that exists specifically to see further back than the panel's own short list).
PAGE_SIZE = 30


@router.get("/unread-count")
async def get_unread_count(
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> JSONResponse:
    return JSONResponse({"unread_count": await count_unread_notifications(conn, user.id)})


@router.get("/recent")
async def get_recent(
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> JSONResponse:
    notifications = await list_recent_notifications(conn, user.id, limit=RECENT_LIMIT)
    return JSONResponse(
        {
            "unread_count": await count_unread_notifications(conn, user.id),
            "notifications": [_to_dict(notification) for notification in notifications],
        }
    )


@router.get("", response_model=None)
async def show_notifications(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    page: Annotated[int, Query(ge=1)] = 1,
) -> HTMLResponse:
    notifications, has_next_page = await list_notifications_page(conn, user.id, page, PAGE_SIZE)
    return templates.TemplateResponse(
        request,
        "notifications.html",
        {
            "active_nav": "notifications",
            "groups": _group(notifications),
            "page": page,
            "has_next_page": has_next_page,
        },
    )


@router.get("/page", response_model=None)
async def notifications_page_fragment(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    page: Annotated[int, Query(ge=1)] = 1,
) -> Response:
    """Just the card markup, no base.html - what notifications-page.js fetches and
    appends as the visitor scrolls, same shape as app/api/library.py's own
    catalog_page_fragment."""
    notifications, has_next_page = await list_notifications_page(conn, user.id, page, PAGE_SIZE)
    response = templates.TemplateResponse(
        request, "_notification_groups.html", {"groups": _group(notifications)}
    )
    response.headers["X-Has-Next-Page"] = "true" if has_next_page else "false"
    return response


@router.get("/panel", response_model=None)
async def notifications_panel_fragment(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> Response:
    """PR 311: the bell panel's list as card markup - the unread notifications
    /recent returns as JSON, rendered by the same macro as the page. The unread count
    for the badge and the panel header travels in X-Unread-Count."""
    notifications = await list_recent_notifications(conn, user.id, limit=RECENT_LIMIT)
    now = datetime.now(UTC)
    response = templates.TemplateResponse(
        request,
        "_notification_panel_list.html",
        {"notifications": [_to_template_context(n, now) for n in notifications]},
    )
    response.headers["X-Unread-Count"] = str(await count_unread_notifications(conn, user.id))
    return response


@router.post("/{notification_id}/read")
async def mark_read(
    notification_id: int,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> JSONResponse:
    if not await mark_notification_read(conn, notification_id, user.id):
        raise HTTPException(status_code=404, detail="Уведомление не найдено")
    return JSONResponse({"unread_count": await count_unread_notifications(conn, user.id)})


@router.delete("/{notification_id}")
async def delete(
    notification_id: int,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> JSONResponse:
    if not await delete_notification(conn, notification_id, user.id):
        raise HTTPException(status_code=404, detail="Уведомление не найдено")
    return JSONResponse({"unread_count": await count_unread_notifications(conn, user.id)})


def _group(notifications: list[Notification]) -> list[dict[str, Any]]:
    """«Новые» (unread) and «Ранее» (read) - list_notifications_page() already returns
    unread first, so each group is one contiguous run; an empty group is left out.
    notifications-page.js merges a next page's groups into the ones already shown."""
    now = datetime.now(UTC)
    groups = []
    for key, title, is_read in (("new", "Новые", False), ("earlier", "Ранее", True)):
        items = [_to_template_context(n, now) for n in notifications if n.is_read == is_read]
        if items:
            groups.append({"key": key, "title": title, "notifications": items})
    return groups


def _to_template_context(notification: Notification, now: datetime) -> dict[str, Any]:
    """What _notification_card.html renders - the same fields as _to_dict() below plus
    the server-formatted relative time (<time datetime> keeps the exact one)."""
    created_at = datetime.fromisoformat(notification.created_at)
    return {
        "id": notification.id,
        "kind": notification.kind,
        "is_read": notification.is_read,
        "actor_name": notification.actor_name,
        "actor_user_id": notification.actor_user_id,
        "actor_avatar_url": notification.actor_avatar_url,
        "actor_avatar_initials": notification.actor_avatar_initials,
        "comment_excerpt": notification.comment_excerpt,
        "comment_url": notification.comment_url,
        "created_at": notification.created_at,
        "created_at_relative": relative_time(created_at, now),
    }


_MONTHS = "янв. февр. мар. апр. мая июн. июл. авг. сент. окт. нояб. дек.".split()


def relative_time(moment: datetime, now: datetime) -> str:
    """«только что» / «5 мин назад» / «3 ч назад» / «2 дн. назад», then a short date
    («3 окт.», with the year once it's not this one). Durations only - the server
    doesn't know the visitor's time zone, so no «вчера» that could be the wrong day."""
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 60:
        return "только что"
    if seconds < 3600:
        return f"{seconds // 60} мин назад"
    if seconds < 86400:
        return f"{seconds // 3600} ч назад"
    if seconds < 7 * 86400:
        return f"{seconds // 86400} дн. назад"
    date = f"{moment.day} {_MONTHS[moment.month - 1]}"
    return date if moment.year == now.year else f"{date} {moment.year}"


def _to_dict(notification: Notification) -> dict[str, Any]:
    return {
        "id": notification.id,
        "kind": notification.kind,
        "is_read": notification.is_read,
        "created_at": notification.created_at,
        "actor_name": notification.actor_name,
        "actor_user_id": notification.actor_user_id,
        "actor_avatar_url": notification.actor_avatar_url,
        "actor_avatar_initials": notification.actor_avatar_initials,
        "comment_id": notification.comment_id,
        "comment_excerpt": notification.comment_excerpt,
        "comment_url": notification.comment_url,
    }

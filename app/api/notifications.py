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
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

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
) -> JSONResponse:
    conn = get_connection()
    return JSONResponse({"unread_count": count_unread_notifications(conn, user.id)})


@router.get("/recent")
async def get_recent(user: Annotated[User, Depends(require_current_user)]) -> JSONResponse:
    conn = get_connection()
    notifications = list_recent_notifications(conn, user.id, limit=RECENT_LIMIT)
    return JSONResponse(
        {
            "unread_count": count_unread_notifications(conn, user.id),
            "notifications": [_to_dict(notification) for notification in notifications],
        }
    )


@router.get("", response_model=None)
async def show_notifications(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
) -> HTMLResponse:
    conn = get_connection()
    notifications, has_next_page = list_notifications_page(conn, user.id, page, PAGE_SIZE)
    return templates.TemplateResponse(
        request,
        "notifications.html",
        {
            "notifications": [_to_template_context(n) for n in notifications],
            "page": page,
            "has_next_page": has_next_page,
        },
    )


@router.get("/page", response_model=None)
async def notifications_page_fragment(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
) -> Response:
    """Just the card markup, no base.html - what notifications-page.js fetches and
    appends as the visitor scrolls, same shape as app/api/library.py's own
    catalog_page_fragment."""
    conn = get_connection()
    notifications, has_next_page = list_notifications_page(conn, user.id, page, PAGE_SIZE)
    response = templates.TemplateResponse(
        request,
        "_notification_cards.html",
        {"notifications": [_to_template_context(n) for n in notifications]},
    )
    response.headers["X-Has-Next-Page"] = "true" if has_next_page else "false"
    return response


@router.post("/{notification_id}/read")
async def mark_read(
    notification_id: int, user: Annotated[User, Depends(require_current_user)]
) -> JSONResponse:
    conn = get_connection()
    if not mark_notification_read(conn, notification_id, user.id):
        raise HTTPException(status_code=404, detail="Уведомление не найдено")
    return JSONResponse({"unread_count": count_unread_notifications(conn, user.id)})


@router.delete("/{notification_id}")
async def delete(
    notification_id: int, user: Annotated[User, Depends(require_current_user)]
) -> JSONResponse:
    conn = get_connection()
    if not delete_notification(conn, notification_id, user.id):
        raise HTTPException(status_code=404, detail="Уведомление не найдено")
    return JSONResponse({"unread_count": count_unread_notifications(conn, user.id)})


def _to_template_context(notification: Notification) -> dict[str, Any]:
    """What notifications.html/_notification_cards.html render - the same fields as
    _to_dict() below plus a server-formatted created_at, since these are plain server-
    rendered <a>/<div> cards rather than notifications-panel.js's client-rendered ones."""
    return {
        "id": notification.id,
        "is_read": notification.is_read,
        "actor_name": notification.actor_name,
        "actor_avatar_url": notification.actor_avatar_url,
        "actor_avatar_initials": notification.actor_avatar_initials,
        "comment_excerpt": notification.comment_excerpt,
        "comment_url": notification.comment_url,
        "created_at_display": _format_time(notification.created_at),
    }


def _format_time(iso: str) -> str:
    """Same day/month/year/hour/minute shape as notifications-panel.js's own formatTime()
    - kept in sync by hand, same as every other piece of formatting this codebase
    duplicates once on each side of the server/client line rather than sharing."""
    return datetime.fromisoformat(iso).strftime("%d.%m.%Y %H:%M")


def _to_dict(notification: Notification) -> dict[str, Any]:
    return {
        "id": notification.id,
        "kind": notification.kind,
        "is_read": notification.is_read,
        "created_at": notification.created_at,
        "actor_name": notification.actor_name,
        "actor_avatar_url": notification.actor_avatar_url,
        "actor_avatar_initials": notification.actor_avatar_initials,
        "comment_id": notification.comment_id,
        "comment_excerpt": notification.comment_excerpt,
        "comment_url": notification.comment_url,
    }

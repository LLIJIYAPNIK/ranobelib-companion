"""PR 168: the sidebar bell's own two endpoints - a lightweight unread count polled on
every page (same idea as app/api/downloads_section.py's /downloads/status) and the
recent-list fetched once when the panel itself opens. Marking read/deleting is PR 170's;
the full "Все уведомления" page is PR 169's - both read the same app/db/notifications.py
this already does.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.auth.dependencies import require_current_user
from app.db.connection import get_connection
from app.db.notifications import (
    Notification,
    count_unread_notifications,
    list_recent_notifications,
)
from app.db.users import User

router = APIRouter(prefix="/notifications")

# How many notifications the panel shows - PR 169's "Все уведомления" page is where
# seeing further back belongs, not a longer list crammed into this same small panel.
RECENT_LIMIT = 20


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

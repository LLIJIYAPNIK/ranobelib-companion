"""The "Активность" section: today's reading/download activity for the logged-in user."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form
from fastapi.responses import Response

from app.auth.dependencies import require_current_user
from app.db.activity import record_heartbeat
from app.db.connection import get_connection
from app.db.users import User

router = APIRouter(prefix="/activity")

_MAX_HEARTBEAT_SECONDS = 60
"""Clamp on a single tick, matching activity-heartbeat.js's own interval (see
app/static/js/activity-heartbeat.js) - keeps a single request from inflating a user's own
active-time stat past what one real interval could ever produce."""


@router.post("/heartbeat", status_code=204)
async def heartbeat(
    user: Annotated[User, Depends(require_current_user)],
    slug_url: Annotated[str, Form()],
    seconds: Annotated[int, Form(gt=0, le=_MAX_HEARTBEAT_SECONDS)],
) -> Response:
    record_heartbeat(get_connection(), user.id, slug_url, seconds)
    return Response(status_code=204)

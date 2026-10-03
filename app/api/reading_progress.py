"""POST /reading-progress/tick - the paragraph-level reading position (wave 35).

The chapter GET already records which chapter a user is in (record_progress(), PR 27),
but the paragraph only exists in the reading page's localStorage. Without this tick the
server would learn it once per page open at best - reading-progress-tick.js sends it as
the reader moves through the chapter instead, throttled the same way
activity-heartbeat.js keeps POST /activity/heartbeat lightweight. Same write path as the
GET (record_progress()), not a parallel one.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import Response
from psycopg import AsyncConnection

from app.auth.dependencies import require_current_user
from app.db.connection import get_connection
from app.db.library import record_progress
from app.db.users import User

router = APIRouter(prefix="/reading-progress")


@router.post("/tick", status_code=204)
async def tick(
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    slug_url: Annotated[str, Form()],
    volume: Annotated[str, Form()],
    number: Annotated[str, Form()],
    paragraph: Annotated[int, Form(ge=1)],
    paragraph_total: Annotated[int, Form(ge=1)],
) -> Response:
    if paragraph > paragraph_total:
        raise HTTPException(status_code=422, detail="paragraph is past paragraph_total")
    # A no-op for a title outside the library - the chapter GET has already added it by
    # the time its page can send a tick (see record_progress()).
    await record_progress(
        conn,
        user.id,
        slug_url,
        volume,
        number,
        paragraph=paragraph,
        paragraph_total=paragraph_total,
    )
    return Response(status_code=204)

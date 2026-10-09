"""POST /reading-progress/tick - the paragraph-level reading position (wave 35).

The chapter GET already records which chapter a user is in (record_progress(), PR 27),
but the paragraph only exists in the reading page's localStorage. Without this tick the
server would learn it once per page open at best - reading-progress-tick.js sends it as
the reader moves through the chapter instead, throttled the same way
activity-heartbeat.js keeps POST /activity/heartbeat lightweight. Same columns as the
GET writes (record_progress()), not a parallel store.

PR 332: a tick read offline arrives later, from the device's queue (sync-queue.js), with
`age_ms` - how long ago it was read. record_position_tick() keeps PR 287's «кто дальше,
тот и победил» for such a late tick, so it can't undo reading the server has seen since.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import Response
from psycopg import AsyncConnection

from app.auth.dependencies import require_current_user
from app.db.connection import get_connection
from app.db.library import record_position_tick
from app.db.users import User
from app.queued_events import MAX_EVENT_AGE_MS, occurred_at

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
    age_ms: Annotated[int | None, Form(ge=0, le=MAX_EVENT_AGE_MS)] = None,
) -> Response:
    if paragraph > paragraph_total:
        raise HTTPException(status_code=422, detail="paragraph is past paragraph_total")
    # A no-op for a title outside the library - the chapter GET has already added it by
    # the time its page can send a tick (see record_progress()).
    await record_position_tick(
        conn, user.id, slug_url, volume, number, paragraph, paragraph_total, occurred_at(age_ms)
    )
    return Response(status_code=204)

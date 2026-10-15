"""``POST /reports`` - a reader reports a comment or another user's profile (PR 356).

JSON in both directions of success and failure: the report form (PR 357) posts here
from a dialog/bottom sheet and shows the ``detail`` of a refusal as is, so every refusal
says in plain words why. Titles and annotations can't be reported - their text comes
from ranobelib.me through the SDK, there's nothing for us to moderate.

The response never names anyone, and nothing here notifies the reported author.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_current_user
from app.db.connection import connection
from app.db.reports import (
    comment_target,
    file_report,
    open_report,
    reports_filed_last_day,
)
from app.db.users import User, get_user_by_id
from app.reports import DAILY_LIMIT, DETAILS_LIMIT, REPORT_REASONS

router = APIRouter(prefix="/reports")


@router.post("")
async def post_report(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)],
    target_type: Annotated[Literal["comment", "profile"], Form()],
    target_id: Annotated[int, Form(ge=1, le=2**31 - 1)],
    reason: Annotated[str, Form(max_length=32)],
    details: Annotated[str, Form(max_length=DETAILS_LIMIT * 4)] = "",
) -> JSONResponse:
    """201 for a new report; 200 when this reader's open report on the object already
    exists - its reason and details are replaced, and the form says «Вы уже отправили
    жалобу» rather than counting a second one. 429 past DAILY_LIMIT new reports a day.

    Not ``require_current_user``: that redirects to /login, and a fetch() from the form
    needs a status and a message instead."""
    if user is None:
        if getattr(request.state, "active_ban", None) is not None:
            raise HTTPException(status_code=403, detail="Аккаунт заблокирован")
        raise HTTPException(status_code=401, detail="Войдите, чтобы отправить жалобу")
    if not user.is_email_verified:
        raise HTTPException(status_code=403, detail="Подтвердите email, чтобы отправлять жалобы")
    if reason not in REPORT_REASONS:
        raise HTTPException(status_code=400, detail="Выберите причину жалобы")
    details = details.strip()
    if len(details) > DETAILS_LIMIT:
        raise HTTPException(status_code=400, detail=f"Пояснение длиннее {DETAILS_LIMIT} символов")

    async with connection() as conn:
        if target_type == "comment":
            comment = await comment_target(conn, target_id)
            if comment is None or comment.deleted:
                raise HTTPException(status_code=404, detail="Комментарий не найден")
            if comment.user_id == user.id:
                raise HTTPException(
                    status_code=400, detail="Нельзя пожаловаться на свой комментарий"
                )
            if comment.hidden:
                raise HTTPException(status_code=409, detail="Комментарий уже скрыт")
        else:
            if target_id == user.id:
                raise HTTPException(status_code=400, detail="Нельзя пожаловаться на свой профиль")
            if await get_user_by_id(conn, target_id) is None:
                raise HTTPException(status_code=404, detail="Пользователь не найден")

        # The daily limit is for new reports: a reader may still change the reason of
        # one already open. Counted in the database, so a restart doesn't reset it.
        if (
            await open_report(conn, user.id, target_type, target_id) is None
            and await reports_filed_last_day(conn, user.id) >= DAILY_LIMIT
        ):
            raise HTTPException(
                status_code=429,
                detail="Слишком много жалоб за сутки - попробуйте завтра",
            )
        _, created = await file_report(
            conn, user.id, target_type, target_id, reason=reason, details=details
        )
    if created:
        return JSONResponse({"status": "sent", "message": "Жалоба отправлена"}, 201)
    return JSONResponse({"status": "already_sent", "message": "Вы уже отправили жалобу"})

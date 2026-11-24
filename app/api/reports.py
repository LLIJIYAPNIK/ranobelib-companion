"""``POST /reports`` - a reader reports a comment, another user's profile, or a published
user title (PR 356, extended in issue 434).

JSON in both directions of success and failure: the report form (PR 357) posts here
from a dialog/bottom sheet and shows the ``detail`` of a refusal as is, so every refusal
says in plain words why. External ranobelib.me titles and annotations can't be reported -
their text comes through the SDK, there's nothing for us to moderate.

The response never names anyone, and nothing here notifies the reported author.
"""

from __future__ import annotations

import uuid
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
    user_title_target,
)
from app.db.user_titles import VISIBILITY_HIDDEN, VISIBILITY_PUBLISHED
from app.db.users import User, get_user_by_id
from app.reports import DAILY_LIMIT, DETAILS_LIMIT, REPORT_REASONS

router = APIRouter(prefix="/reports")

_MAX_INT32 = 2**31 - 1


def _parse_target_id(target_type: str, raw: str) -> int | uuid.UUID:
    raw = raw.strip()
    if target_type == "user_title":
        try:
            return uuid.UUID(raw)
        except ValueError:
            raise HTTPException(
                status_code=422, detail="Некорректный идентификатор книги"
            ) from None
    try:
        value = int(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail="Некорректный идентификатор") from None
    if value < 1 or value > _MAX_INT32:
        raise HTTPException(status_code=422, detail="Некорректный идентификатор")
    return value


@router.post("")
async def post_report(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)],
    target_type: Annotated[Literal["comment", "profile", "user_title"], Form()],
    target_id: Annotated[str, Form(min_length=1, max_length=64)],
    reason: Annotated[str, Form(max_length=32)],
    details: Annotated[str, Form(max_length=DETAILS_LIMIT * 4)] = "",
) -> JSONResponse:
    """201 for a new report; 200 when this reader's open report on the object already
    exists - its reason and details are replaced, and the form says «Вы уже отправили
    жалобу» rather than counting a second one. 429 past DAILY_LIMIT new reports a day.

    Not ``require_current_user``: that redirects to /login, and a fetch() from the form
    needs a status and a message instead."""
    parsed_target = _parse_target_id(target_type, target_id)
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
            assert isinstance(parsed_target, int)
            comment = await comment_target(conn, parsed_target)
            if comment is None or comment.deleted:
                raise HTTPException(status_code=404, detail="Комментарий не найден")
            if comment.user_id == user.id:
                raise HTTPException(
                    status_code=400, detail="Нельзя пожаловаться на свой комментарий"
                )
            if comment.hidden:
                raise HTTPException(status_code=409, detail="Комментарий уже скрыт")
        elif target_type == "profile":
            assert isinstance(parsed_target, int)
            if parsed_target == user.id:
                raise HTTPException(status_code=400, detail="Нельзя пожаловаться на свой профиль")
            if await get_user_by_id(conn, parsed_target) is None:
                raise HTTPException(status_code=404, detail="Пользователь не найден")
        else:
            assert isinstance(parsed_target, uuid.UUID)
            ut = await user_title_target(conn, parsed_target)
            if ut is None or ut.visibility not in (VISIBILITY_PUBLISHED, VISIBILITY_HIDDEN):
                raise HTTPException(status_code=404, detail="Книга не найдена")
            if ut.user_id == user.id:
                raise HTTPException(status_code=400, detail="Нельзя пожаловаться на свою книгу")
            if ut.visibility == VISIBILITY_HIDDEN:
                raise HTTPException(status_code=409, detail="Книга уже скрыта")

        # The daily limit is for new reports: a reader may still change the reason of
        # one already open. Counted in the database, so a restart doesn't reset it.
        if (
            await open_report(conn, user.id, target_type, parsed_target) is None
            and await reports_filed_last_day(conn, user.id) >= DAILY_LIMIT
        ):
            raise HTTPException(
                status_code=429,
                detail="Слишком много жалоб за сутки - попробуйте завтра",
            )
        _, created = await file_report(
            conn, user.id, target_type, parsed_target, reason=reason, details=details
        )
    if created:
        return JSONResponse({"status": "sent", "message": "Жалоба отправлена"}, 201)
    return JSONResponse({"status": "already_sent", "message": "Вы уже отправили жалобу"})

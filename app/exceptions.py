"""Maps ranobelib.RanobeLibError subclasses to HTTP responses.

See CLAUDE.md, "Обработка ошибок", for the table this implements. The exception's own
message (which may embed technical/internal detail) never reaches the response body —
only the fixed, user-facing text per exception type; callers are expected to log the
original exception separately.

MultipleTranslationsError / MultipleTitleTranslationsError use 409 Conflict (not 300
Multiple Choices, which the CLAUDE.md table also allowed): 409 is the conventional choice
for "can't complete the request without more input from the client" in a JSON API, and
doesn't carry 300's baggage of clients expecting a Location-style redirect.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from ranobelib import (
    AuthRequiredError,
    ChapterNotFoundError,
    MultipleTitleTranslationsError,
    MultipleTranslationsError,
    RanobeLibError,
    RateLimitError,
    TitleNotFoundError,
    VolumeNotFoundError,
)

from app.templating import templates

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ErrorResponse:
    status_code: int
    content: dict[str, Any]


def build_error_response(exc: RanobeLibError) -> ErrorResponse:
    """Turn an SDK exception into the (status_code, body) this app shows the user."""
    if isinstance(exc, TitleNotFoundError):
        return ErrorResponse(404, {"detail": "Тайтл не найден, проверьте ссылку"})

    if isinstance(exc, ChapterNotFoundError):
        return ErrorResponse(404, {"detail": "Глава не найдена"})

    if isinstance(exc, VolumeNotFoundError):
        return ErrorResponse(404, {"detail": "Том не найден"})

    if isinstance(exc, MultipleTranslationsError):
        return ErrorResponse(
            409,
            {
                "detail": "У главы несколько переводов, выберите один",
                "volume": exc.volume,
                "number": exc.number,
                "branches": jsonable_encoder(exc.branches),
            },
        )

    if isinstance(exc, MultipleTitleTranslationsError):
        return ErrorResponse(
            409,
            {
                "detail": "У части глав несколько переводов, выберите перевод для каждой",
                "chapters": jsonable_encoder(
                    [
                        {
                            "volume": chapter.volume,
                            "number": chapter.number,
                            "branches": chapter.branches,
                        }
                        for chapter in exc.chapters
                    ]
                ),
            },
        )

    if isinstance(exc, AuthRequiredError):
        return ErrorResponse(403, {"detail": "Требуется авторизация — недоступно"})

    if isinstance(exc, RateLimitError):
        return ErrorResponse(
            429, {"detail": "ranobelib сейчас ограничивает запросы, попробуйте позже"}
        )

    # Any other RanobeLibError subclass the table above doesn't cover.
    return ErrorResponse(500, {"detail": "Внутренняя ошибка, попробуйте позже"})


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the RanobeLibError -> HTTP mapping to `app`. Route handlers must not catch
    RanobeLibError themselves — this is the single place that does."""
    app.add_exception_handler(RanobeLibError, _handle_ranobelib_error)


async def _handle_ranobelib_error(request: Request, exc: RanobeLibError) -> Response:
    logger.warning(
        "%s on %s %s", type(exc).__name__, request.method, request.url.path, exc_info=exc
    )
    error = build_error_response(exc)
    if _wants_html(request):
        if isinstance(exc, MultipleTranslationsError):
            return templates.TemplateResponse(
                request,
                "choose_translation.html",
                {
                    "slug_url": exc.slug_url,
                    "volume": exc.volume,
                    "number": exc.number,
                    "branches": exc.branches,
                    # branch_id isn't in the request that failed - the same route with it
                    # added as a query param is exactly what should be retried.
                    "target_path": request.url.path,
                },
                status_code=error.status_code,
            )
        return templates.TemplateResponse(
            request,
            "error.html",
            {"status_code": error.status_code, "detail": error.content["detail"]},
            status_code=error.status_code,
        )
    return JSONResponse(status_code=error.status_code, content=error.content)


def _wants_html(request: Request) -> bool:
    """True for a browser page navigation, so it gets an error.html page instead of raw
    JSON - this app is server-rendered, not a JSON API consumed by a separate client."""
    return "text/html" in request.headers.get("accept", "")

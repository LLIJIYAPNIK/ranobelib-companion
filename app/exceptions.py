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

from dataclasses import dataclass
from typing import Any

from fastapi.encoders import jsonable_encoder
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

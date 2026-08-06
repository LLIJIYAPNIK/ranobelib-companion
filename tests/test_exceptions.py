from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from ranobelib import (
    AuthRequiredError,
    ChapterNotFoundError,
    MultipleTitleTranslationsError,
    MultipleTranslationsError,
    RateLimitError,
    TitleNotFoundError,
    VolumeNotFoundError,
)
from ranobelib.exceptions import AmbiguousChapter
from ranobelib.models import ChapterBranch, ChapterUser

from app.exceptions import register_exception_handlers


def _branch(branch_id: int) -> ChapterBranch:
    return ChapterBranch(
        id=branch_id,
        branch_id=branch_id,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        teams=[],
        user=ChapterUser(id=branch_id, username=f"user{branch_id}"),
    )


def _client_raising(exc: Exception) -> TestClient:
    """A throwaway app whose one route raises `exc`, wired the same way app.main is."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise")
    async def raise_it() -> None:
        raise exc

    return TestClient(app, raise_server_exceptions=False)


def test_title_not_found() -> None:
    response = _client_raising(TitleNotFoundError("123--slug")).get("/raise")

    assert response.status_code == 404
    assert response.json() == {"detail": "Тайтл не найден, проверьте ссылку"}


def test_chapter_not_found() -> None:
    exc = ChapterNotFoundError("123--slug", volume="1", number="5")
    response = _client_raising(exc).get("/raise")

    assert response.status_code == 404
    assert response.json() == {"detail": "Глава не найдена"}


def test_volume_not_found() -> None:
    response = _client_raising(VolumeNotFoundError("123--slug", volume="1")).get("/raise")

    assert response.status_code == 404
    assert response.json() == {"detail": "Том не найден"}


def test_multiple_translations() -> None:
    branches = [_branch(1), _branch(2)]
    exc = MultipleTranslationsError("123--slug", volume="1", number="5", branches=branches)
    response = _client_raising(exc).get("/raise")

    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == "У главы несколько переводов, выберите один"
    assert body["volume"] == "1"
    assert body["number"] == "5"
    assert [branch["branch_id"] for branch in body["branches"]] == [1, 2]


def test_multiple_title_translations() -> None:
    chapters = [
        AmbiguousChapter(volume="1", number="5", branches=[_branch(1), _branch(2)]),
        AmbiguousChapter(volume="1", number="6", branches=[_branch(3)]),
    ]
    exc = MultipleTitleTranslationsError("123--slug", chapters=chapters)
    response = _client_raising(exc).get("/raise")

    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == "У части глав несколько переводов, выберите перевод для каждой"
    assert [c["volume"] for c in body["chapters"]] == ["1", "1"]
    assert [c["number"] for c in body["chapters"]] == ["5", "6"]
    assert [branch["branch_id"] for branch in body["chapters"][0]["branches"]] == [1, 2]
    assert [branch["branch_id"] for branch in body["chapters"][1]["branches"]] == [3]


def test_auth_required() -> None:
    response = _client_raising(AuthRequiredError("https://ranobelib.me/x")).get("/raise")

    assert response.status_code == 403
    assert response.json() == {"detail": "Требуется авторизация — недоступно"}


def test_rate_limit() -> None:
    response = _client_raising(RateLimitError(retry_after=30)).get("/raise")

    assert response.status_code == 429
    assert response.json() == {
        "detail": "ranobelib сейчас ограничивает запросы, попробуйте позже"
    }

from unittest.mock import patch

from fastapi.testclient import TestClient
from ranobelib import ChapterNotFoundError
from ranobelib.models import Chapter

from app.main import app

client = TestClient(app)


class _FakeClient:
    def __init__(self, chapter: Chapter | None = None, exc: Exception | None = None) -> None:
        self._chapter = chapter
        self._exc = exc

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_chapter(self, volume: int, number: str) -> Chapter:
        if self._exc is not None:
            raise self._exc
        assert self._chapter is not None
        return self._chapter


def test_read_chapter_renders_heading_and_content() -> None:
    chapter = Chapter(
        id=1, volume="1", number="5", name="Начало", content="<p>Текст главы</p>"
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 200
    assert "Глава 5" in response.text
    assert "Начало" in response.text
    assert 'href="/titles/6712--test-novel"' in response.text
    assert "<p>Текст главы</p>" in response.text


def test_read_chapter_sanitizes_content() -> None:
    chapter = Chapter(
        id=1,
        volume="1",
        number="5",
        name="Начало",
        content="<p>hi</p><script>alert('xss')</script>",
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 200
    assert "<script>" not in response.text
    assert "alert" not in response.text


def test_read_chapter_not_found() -> None:
    exc = ChapterNotFoundError("6712--test-novel", volume="1", number="999")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(exc=exc)):
        response = client.get(
            "/titles/6712--test-novel/chapters/1/999", headers={"accept": "text/html"}
        )

    assert response.status_code == 404
    assert "Глава не найдена" in response.text

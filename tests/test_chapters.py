from datetime import UTC, datetime
from unittest.mock import patch

from fastapi.testclient import TestClient
from ranobelib import ChapterNotFoundError, MultipleTranslationsError
from ranobelib.models import Chapter, ChapterBranch, ChapterUser, Team, Volume

from app.main import app

client = TestClient(app)


class _FakeClient:
    def __init__(
        self,
        chapter: Chapter | None = None,
        exc: Exception | None = None,
        volumes: list[Volume] | None = None,
    ) -> None:
        self._chapter = chapter
        self._exc = exc
        self._volumes = volumes or []
        self.received_branch_id: int | None | str = "not called"

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_chapter(
        self, volume: int, number: str, *, branch_id: int | None = None
    ) -> Chapter:
        self.received_branch_id = branch_id
        if self._exc is not None:
            raise self._exc
        assert self._chapter is not None
        return self._chapter

    async def get_table_of_contents(self) -> list[Volume]:
        return self._volumes


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


def test_read_chapter_shows_adjacent_chapter_links() -> None:
    chapter = Chapter(id=2, volume="1", number="2", name="Середина", content="<p>x</p>")
    volumes = [
        Volume(
            number="1",
            chapters=[
                Chapter(id=1, volume="1", number="1"),
                Chapter(id=2, volume="1", number="2"),
                Chapter(id=3, volume="1", number="3"),
            ],
        )
    ]
    with patch(
        "app.services.client.RanobeLib",
        return_value=_FakeClient(chapter, volumes=volumes),
    ):
        response = client.get("/titles/6712--test-novel/chapters/1/2")

    assert response.status_code == 200
    assert 'href="/titles/6712--test-novel/chapters/1/1"' in response.text
    assert 'href="/titles/6712--test-novel/chapters/1/3"' in response.text


def test_read_chapter_crosses_volume_boundary() -> None:
    chapter = Chapter(id=1, volume="1", number="3", name="Конец тома", content="<p>x</p>")
    volumes = [
        Volume(number="1", chapters=[Chapter(id=1, volume="1", number="3")]),
        Volume(number="2", chapters=[Chapter(id=2, volume="2", number="1")]),
    ]
    with patch(
        "app.services.client.RanobeLib",
        return_value=_FakeClient(chapter, volumes=volumes),
    ):
        response = client.get("/titles/6712--test-novel/chapters/1/3")

    assert response.status_code == 200
    assert "Предыдущая глава" not in response.text
    assert 'href="/titles/6712--test-novel/chapters/2/1"' in response.text


def test_read_chapter_not_found() -> None:
    exc = ChapterNotFoundError("6712--test-novel", volume="1", number="999")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(exc=exc)):
        response = client.get(
            "/titles/6712--test-novel/chapters/1/999", headers={"accept": "text/html"}
        )

    assert response.status_code == 404
    assert "Глава не найдена" in response.text


def test_read_chapter_multiple_translations_shows_choice_page() -> None:
    branches = [
        ChapterBranch(
            id=1,
            branch_id=1,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            teams=[Team(id=1, slug="team-a", slug_url="team-a", name="Команда А")],
            user=ChapterUser(id=1, username="uploader1"),
        ),
        ChapterBranch(
            id=2,
            branch_id=2,
            created_at=datetime(2024, 3, 1, tzinfo=UTC),
            teams=[],
            user=ChapterUser(id=2, username="solo_translator"),
        ),
    ]
    exc = MultipleTranslationsError(
        "6712--test-novel", volume="1", number="5", branches=branches
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(exc=exc)):
        response = client.get(
            "/titles/6712--test-novel/chapters/1/5", headers={"accept": "text/html"}
        )

    assert response.status_code == 409
    assert "Команда А" in response.text
    assert "solo_translator" in response.text
    assert 'href="/titles/6712--test-novel/chapters/1/5?branch_id=1"' in response.text
    assert 'href="/titles/6712--test-novel/chapters/1/5?branch_id=2"' in response.text


def test_read_chapter_passes_branch_id_from_query_to_sdk() -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    fake = _FakeClient(chapter)
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get("/titles/6712--test-novel/chapters/1/5?branch_id=42")

    assert response.status_code == 200
    assert fake.received_branch_id == 42


def test_read_chapter_passes_no_branch_id_by_default() -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    fake = _FakeClient(chapter)
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 200
    assert fake.received_branch_id is None

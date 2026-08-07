import os
from unittest.mock import patch

from fastapi.testclient import TestClient
from ranobelib.models import Chapter

from app.main import app

client = TestClient(app)


class _FakeClient:
    def __init__(self, chapter: Chapter) -> None:
        self._chapter = chapter
        self.received_branch_id: int | None | str = "not called"
        self.export_calls: list[tuple[list[Chapter], str, str]] = []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_chapter(
        self, volume: int, number: str, *, branch_id: int | None = None
    ) -> Chapter:
        self.received_branch_id = branch_id
        return self._chapter

    async def export(self, chapters: list[Chapter], *, fmt: str, path: str) -> str:
        self.export_calls.append((chapters, fmt, path))
        with open(path, "w", encoding="utf-8") as f:
            f.write("exported content")
        return path


def test_export_chapter_returns_file_and_cleans_up_afterwards() -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    fake = _FakeClient(chapter)
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get("/titles/6712--test-novel/chapters/1/5/export?fmt=txt")

    assert response.status_code == 200
    assert response.content == b"exported content"
    assert 'filename="6712--test-novel--1-5.txt"' in response.headers["content-disposition"]

    assert len(fake.export_calls) == 1
    exported_chapters, fmt, path = fake.export_calls[0]
    assert exported_chapters == [chapter]
    assert fmt == "txt"
    assert not os.path.exists(path)


def test_export_chapter_passes_branch_id_to_sdk() -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    fake = _FakeClient(chapter)
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get(
            "/titles/6712--test-novel/chapters/1/5/export?fmt=txt&branch_id=42"
        )

    assert response.status_code == 200
    assert fake.received_branch_id == 42


def test_export_chapter_rejects_unknown_format() -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    fake = _FakeClient(chapter)
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get("/titles/6712--test-novel/chapters/1/5/export?fmt=docx")

    assert response.status_code == 400
    assert fake.export_calls == []

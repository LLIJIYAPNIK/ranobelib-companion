import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib.models import Chapter, Volume

from app.main import app

client = TestClient(app)


class _FakeClient:
    def __init__(
        self,
        chapter: Chapter,
        chapters: list[Chapter] | None = None,
        volume: Volume | None = None,
        volumes: list[Volume] | None = None,
    ) -> None:
        self._chapter = chapter
        self._chapters = chapters or []
        self._volume = volume
        self._volumes = volumes or []
        self.received_branch_id: int | None | str = "not called"
        self.received_chapter_keys: list[tuple[int, str]] | None = None
        self.received_volume_numbers: list[int] | None = None
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

    async def get_chapters(self, chapters: list[tuple[int, str]]) -> list[Chapter]:
        self.received_chapter_keys = chapters
        return self._chapters

    async def get_volume(self, volume: int) -> Volume:
        assert self._volume is not None
        return self._volume

    async def get_volumes(self, volumes: list[int]) -> list[Volume]:
        self.received_volume_numbers = volumes
        return self._volumes

    async def export(self, chapters: list[Chapter], *, fmt: str, path: str) -> str:
        self.export_calls.append((chapters, fmt, path))
        with open(path, "w", encoding="utf-8") as f:
            f.write("exported content")
        return path


class _FailingExportClient(_FakeClient):
    async def export(self, chapters: list[Chapter], *, fmt: str, path: str) -> str:
        self.export_calls.append((chapters, fmt, path))
        raise RuntimeError("boom")


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


def test_export_chapter_malformed_slug_url_returns_friendly_404_not_500() -> None:
    # No RanobeLib patch here on purpose - see the equivalent test in test_titles.py.
    response = client.get("/titles/not-a-valid-slug/chapters/1/5/export?fmt=txt")

    assert response.status_code == 404
    assert response.json() == {"detail": "Тайтл не найден, проверьте ссылку"}


def test_export_chapters_combines_selected_chapters_into_one_file() -> None:
    chapters = [
        Chapter(id=1, volume="1", number="1", content="<p>a</p>"),
        Chapter(id=2, volume="1", number="2", content="<p>b</p>"),
    ]
    fake = _FakeClient(chapter=chapters[0], chapters=chapters)
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get(
            "/titles/6712--test-novel/export?fmt=epub&chapters=1--1&chapters=1--2"
        )

    assert response.status_code == 200
    assert response.content == b"exported content"
    assert (
        'filename="6712--test-novel--2-chapters.epub"'
        in response.headers["content-disposition"]
    )
    assert fake.received_chapter_keys == [(1, "1"), (1, "2")]
    assert len(fake.export_calls) == 1
    exported_chapters, fmt, path = fake.export_calls[0]
    assert exported_chapters == chapters
    assert fmt == "epub"
    assert not os.path.exists(path)


def test_export_chapters_rejects_unknown_format() -> None:
    fake = _FakeClient(chapter=Chapter(id=1, volume="1", number="1", content="<p>a</p>"))
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get("/titles/6712--test-novel/export?fmt=docx&chapters=1--1")

    assert response.status_code == 400
    assert fake.export_calls == []


def test_export_chapters_rejects_malformed_chapter_key() -> None:
    fake = _FakeClient(chapter=Chapter(id=1, volume="1", number="1", content="<p>a</p>"))
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get("/titles/6712--test-novel/export?fmt=txt&chapters=not-a-key")

    assert response.status_code == 400
    assert fake.export_calls == []


def test_export_chapter_cleans_up_temp_file_when_export_fails() -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    fake = _FailingExportClient(chapter)
    with (
        patch("app.services.client.RanobeLib", return_value=fake),
        pytest.raises(RuntimeError),
    ):
        client.get("/titles/6712--test-novel/chapters/1/5/export?fmt=txt")

    assert len(fake.export_calls) == 1
    path = fake.export_calls[0][2]
    assert not os.path.exists(path)


def test_export_chapters_cleans_up_temp_file_when_export_fails() -> None:
    fake = _FailingExportClient(chapter=Chapter(id=1, volume="1", number="1"))
    with (
        patch("app.services.client.RanobeLib", return_value=fake),
        pytest.raises(RuntimeError),
    ):
        client.get("/titles/6712--test-novel/export?fmt=txt&chapters=1--1")

    assert len(fake.export_calls) == 1
    path = fake.export_calls[0][2]
    assert not os.path.exists(path)


def test_export_chapters_requires_at_least_one_chapter() -> None:
    fake = _FakeClient(chapter=Chapter(id=1, volume="1", number="1", content="<p>a</p>"))
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get("/titles/6712--test-novel/export?fmt=txt")

    assert response.status_code == 422
    assert fake.export_calls == []


def test_export_volume_combines_its_chapters_into_one_file() -> None:
    chapters = [
        Chapter(id=1, volume="1", number="1", content="<p>a</p>"),
        Chapter(id=2, volume="1", number="2", content="<p>b</p>"),
    ]
    volume = Volume(number="1", chapters=chapters)
    fake = _FakeClient(chapter=chapters[0], volume=volume)
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get("/titles/6712--test-novel/volumes/1/export?fmt=epub")

    assert response.status_code == 200
    assert response.content == b"exported content"
    assert (
        'filename="6712--test-novel--volume-1.epub"'
        in response.headers["content-disposition"]
    )
    assert len(fake.export_calls) == 1
    exported_chapters, fmt, path = fake.export_calls[0]
    assert exported_chapters == chapters
    assert fmt == "epub"
    assert not os.path.exists(path)


def test_export_volume_rejects_unknown_format() -> None:
    volume = Volume(number="1", chapters=[])
    fake = _FakeClient(chapter=Chapter(id=1, volume="1", number="1"), volume=volume)
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get("/titles/6712--test-novel/volumes/1/export?fmt=docx")

    assert response.status_code == 400
    assert fake.export_calls == []


def test_export_volumes_combines_several_volumes_into_one_file() -> None:
    chapters_v1 = [Chapter(id=1, volume="1", number="1", content="<p>a</p>")]
    chapters_v2 = [Chapter(id=2, volume="2", number="1", content="<p>b</p>")]
    volumes = [Volume(number="1", chapters=chapters_v1), Volume(number="2", chapters=chapters_v2)]
    fake = _FakeClient(chapter=chapters_v1[0], volumes=volumes)
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get(
            "/titles/6712--test-novel/volumes/export?fmt=epub&volumes=1&volumes=2"
        )

    assert response.status_code == 200
    assert (
        'filename="6712--test-novel--2-volumes.epub"'
        in response.headers["content-disposition"]
    )
    assert fake.received_volume_numbers == [1, 2]
    assert len(fake.export_calls) == 1
    exported_chapters, fmt, path = fake.export_calls[0]
    assert exported_chapters == chapters_v1 + chapters_v2
    assert fmt == "epub"
    assert not os.path.exists(path)


def test_export_volumes_requires_at_least_one_volume() -> None:
    fake = _FakeClient(chapter=Chapter(id=1, volume="1", number="1"))
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get("/titles/6712--test-novel/volumes/export?fmt=txt")

    assert response.status_code == 422
    assert fake.export_calls == []


def test_export_volume_cleans_up_temp_file_when_export_fails() -> None:
    volume = Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1")])
    fake = _FailingExportClient(chapter=Chapter(id=1, volume="1", number="1"), volume=volume)
    with (
        patch("app.services.client.RanobeLib", return_value=fake),
        pytest.raises(RuntimeError),
    ):
        client.get("/titles/6712--test-novel/volumes/1/export?fmt=txt")

    assert len(fake.export_calls) == 1
    path = fake.export_calls[0][2]
    assert not os.path.exists(path)

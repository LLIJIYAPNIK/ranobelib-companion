"""Offline reading's server contract (PR 329): the title manifest and the one-chapter
fragment the browser-side download manager stores on the device."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib import RateLimitError
from ranobelib.models import Chapter, ChapterBranch, ChapterUser, Team, Volume

from app.main import app

client = TestClient(app)

SLUG = "6712--test-novel"


def _branch(branch_id: int, team: str | None) -> ChapterBranch:
    return ChapterBranch(
        id=branch_id,
        branch_id=branch_id,
        created_at=datetime(2024, 1, branch_id, tzinfo=UTC),
        teams=[Team(id=branch_id, slug="t", slug_url="t", name=team)] if team else [],
        user=ChapterUser(id=branch_id, username=f"uploader{branch_id}"),
    )


_VOLUMES = [
    Volume(
        number="1",
        chapters=[
            Chapter(
                id=1, volume="1", number="1", name="Начало", branches=[_branch(1, "Команда А")]
            ),
            Chapter(
                id=2,
                volume="1",
                number="2",
                branches=[_branch(1, "Команда А"), _branch(2, None)],
                branches_count=2,
            ),
        ],
    ),
    Volume(number="2", chapters=[Chapter(id=3, volume="2", number="2.5", name=None)]),
]


class _FakeClient:
    """Records every SDK call, and how many were ever in flight at once."""

    def __init__(
        self,
        *,
        chapter: Chapter | None = None,
        exc: Exception | None = None,
        estimate: int | Exception = 3_000_000,
    ) -> None:
        self._chapter = chapter
        self._exc = exc
        self._estimate = estimate
        self.calls: list[tuple] = []
        self._in_flight = 0
        self.max_in_flight = 0

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def _call(self, *call: object) -> None:
        self.calls.append(call)
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        await asyncio.sleep(0)
        self._in_flight -= 1

    async def get_table_of_contents(self) -> list[Volume]:
        await self._call("get_table_of_contents")
        return _VOLUMES

    async def estimate_title_size(self) -> int:
        await self._call("estimate_title_size")
        if isinstance(self._estimate, Exception):
            raise self._estimate
        return self._estimate

    async def get_chapter(self, volume: int, number: str, *, branch_id: int | None = None):
        await self._call("get_chapter", volume, number, branch_id)
        if self._exc is not None:
            raise self._exc
        return self._chapter


@pytest.fixture
def fake() -> Iterator[_FakeClient]:
    fake = _FakeClient()
    with patch("app.services.client.RanobeLib", return_value=fake):
        yield fake


# --- manifest -----------------------------------------------------------------------


def test_manifest_lists_every_chapter_with_its_translations(fake: _FakeClient) -> None:
    response = client.get(f"/offline/titles/{SLUG}/manifest")

    assert response.status_code == 200
    body = response.json()
    assert body["slug_url"] == SLUG
    assert body["chapter_count"] == 3
    assert [volume["number"] for volume in body["volumes"]] == ["1", "2"]
    first, second = body["volumes"][0]["chapters"]
    assert first == {
        "volume": "1",
        "number": "1",
        "name": "Начало",
        "url": f"/offline/titles/{SLUG}/chapters/1/1",
        "branches": [{"branch_id": 1, "teams": ["Команда А"], "uploader": "uploader1"}],
    }
    # Two translations: both listed, none picked - the client asks the visitor.
    assert [branch["branch_id"] for branch in second["branches"]] == [1, 2]
    assert second["branches"][1]["teams"] == []
    assert body["volumes"][1]["chapters"][0]["url"] == f"/offline/titles/{SLUG}/chapters/2/2.5"


def test_manifest_carries_the_sdk_size_estimate(fake: _FakeClient) -> None:
    body = client.get(f"/offline/titles/{SLUG}/manifest").json()

    assert body["estimated_bytes"] == 3_000_000
    assert body["estimated_bytes_per_chapter"] == 1_000_000


def test_a_failed_estimate_leaves_the_list_intact() -> None:
    fake = _FakeClient(estimate=RateLimitError("slow down"))
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get(f"/offline/titles/{SLUG}/manifest")

    assert response.status_code == 200
    assert response.json()["estimated_bytes"] is None
    assert response.json()["estimated_bytes_per_chapter"] is None
    assert response.json()["chapter_count"] == 3

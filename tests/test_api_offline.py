"""Offline reading's server contract (PR 329): the title manifest and the one-chapter
fragment the browser-side download manager stores on the device."""

import asyncio
import html
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import patch
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from ranobelib import (
    AccessBlockedError,
    AuthRequiredError,
    ChapterNotFoundError,
    MultipleTranslationsError,
    RateLimitError,
    chapter_size,
)
from ranobelib.models import Chapter, ChapterBranch, ChapterUser, Team, Volume

from app.config import get_settings
from app.db.activity import list_chapters_read_today
from app.db.connection import connection
from app.db.library import list_entries
from app.main import app
from app.services.offline import image_urls, rewrite_images
from tests.auth_helpers import register
from tests.db_reset import reset_app_database

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


# --- chapter fragment ---------------------------------------------------------------

_IMG_A = "https://ranobelib.me/uploads/ranobe/1/a.png"
_IMG_B = "https://cover.cdnlibs.org/uploads/b.jpg?w=1&h=2"


def _view(url: str) -> str:
    return "/images/view?url=" + quote(url, safe="")


def _chapter_with_images() -> Chapter:
    # A str content goes through the SDK's own sanitizer, like a real response would.
    return Chapter(
        id=5,
        volume="1",
        number="5",
        name="Глава с картинками",
        content=(
            f'<p>До &lt;img src="x"&gt;</p><p><img src="{_IMG_A}"></p>'
            f'<p><img src="{html.escape(_IMG_B)}"></p><p><img src="{_IMG_A}"></p>'
            f'<p>↑ Сноска <img src="{_IMG_B.replace("&", "&amp;")}"></p>'
        ),
    )


def _fragment(chapter: Chapter, **params: object) -> tuple[_FakeClient, dict]:
    fake = _FakeClient(chapter=chapter)
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get(f"/offline/titles/{SLUG}/chapters/1/5", params=params)
    assert response.status_code == 200, response.text
    return fake, response.json()


def test_fragment_carries_the_chapter_with_images_on_the_proxy() -> None:
    chapter = _chapter_with_images()
    _, body = _fragment(chapter)

    assert body["slug_url"] == SLUG
    assert (body["volume"], body["number"], body["name"]) == ("1", "5", "Глава с картинками")
    assert body["branch_id"] is None
    a = f'<img loading="lazy" src="{html.escape(_view(_IMG_A))}" />'
    b = f'<img loading="lazy" src="{html.escape(_view(_IMG_B))}" />'
    # Every <img> on the same-origin proxy; escaped text that merely looks like a tag
    # stays text.
    assert body["content"] == (
        f"<p>До &lt;img src=&quot;x&quot;&gt;</p><p>{a}</p><p>{b}</p><p>{a}</p>"
    )
    assert body["footnotes"] == [
        f'Сноска <img loading="lazy" src="{html.escape(_view(_IMG_B))}" />'
    ]
    # Each image once, in document order, ready to fetch and store.
    assert body["images"] == [_view(_IMG_A), _view(_IMG_B)]
    assert body["estimated_bytes"] == chapter_size(chapter)


def test_fragment_passes_the_picked_translation_through() -> None:
    fake, body = _fragment(_chapter_with_images(), branch_id=2)

    assert fake.calls == [("get_chapter", 1, "5", 2)]
    assert body["branch_id"] == 2


def test_fragment_without_images_has_an_empty_list() -> None:
    _, body = _fragment(Chapter(id=6, volume="1", number="5", content="<p>Текст</p>"))

    assert body["images"] == []
    assert body["content"] == "<p>Текст</p>"
    assert body["footnotes"] == []


def test_rewrite_handles_the_sdks_unescaped_attachment_tags() -> None:
    # The SDK's ProseMirror path writes attachment URLs into src without escaping "&".
    raw = '<p><img loading="lazy" src="https://ranobelib.me/a.png?x=1&y=2" /></p>'

    assert image_urls(raw) == ["https://ranobelib.me/a.png?x=1&y=2"]
    assert rewrite_images(raw, lambda url: "/v?u=" + url) == (
        '<p><img loading="lazy" src="/v?u=https://ranobelib.me/a.png?x=1&amp;y=2" /></p>'
    )


# --- SDK errors: the central mapping, always as JSON ---------------------------------

_BRANCHES = [_branch(1, "Команда А"), _branch(2, None)]


@pytest.mark.parametrize("accept", ["*/*", "application/json", "text/html,*/*"])
def test_an_ambiguous_chapter_is_a_409_listing_the_translations(accept: str) -> None:
    exc = MultipleTranslationsError(SLUG, volume="1", number="5", branches=_BRANCHES)
    fake = _FakeClient(exc=exc)
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get(f"/offline/titles/{SLUG}/chapters/1/5", headers={"accept": accept})

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert body["detail"] == "У главы несколько переводов, выберите один"
    assert [branch["branch_id"] for branch in body["branches"]] == [1, 2]
    # Nothing picked on the visitor's behalf: one call, without a branch.
    assert fake.calls == [("get_chapter", 1, "5", None)]


@pytest.mark.parametrize(
    ("exc", "status", "detail"),
    [
        (ChapterNotFoundError(SLUG, volume="1", number="5"), 404, "Глава не найдена"),
        (AuthRequiredError("paid"), 403, "Требуется авторизация — недоступно"),
        (RateLimitError("slow"), 429, "ranobelib сейчас ограничивает запросы, попробуйте позже"),
        (
            AccessBlockedError("ddos-guard"),
            503,
            "Источник тайтлов временно блокирует наши запросы, попробуйте позже",
        ),
    ],
)
def test_chapter_errors_map_through_the_central_handler(
    exc: Exception, status: int, detail: str
) -> None:
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(exc=exc)):
        response = client.get(
            f"/offline/titles/{SLUG}/chapters/1/5", headers={"accept": "text/html,*/*"}
        )

    assert response.status_code == status
    # The user-facing text only - never the exception's own (technical) message.
    assert response.json() == {"detail": detail}


def test_an_unknown_title_is_a_404_for_the_manifest() -> None:
    response = client.get("/offline/titles/not-a-slug/manifest")

    assert response.status_code == 404
    assert response.json() == {"detail": "Тайтл не найден, проверьте ссылку"}


def test_the_online_reader_still_shows_its_html_pages() -> None:
    exc = MultipleTranslationsError(SLUG, volume="1", number="5", branches=_BRANCHES)
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(exc=exc)):
        response = client.get(f"/titles/{SLUG}/chapters/1/5", headers={"accept": "text/html"})

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("text/html")


# --- no second cache, no parallel SDK calls ------------------------------------------


def test_each_request_goes_to_the_sdk_nothing_is_kept_on_the_server() -> None:
    fake = _FakeClient(chapter=_chapter_with_images())
    with patch("app.services.client.RanobeLib", return_value=fake) as sdk:
        first = client.get(f"/offline/titles/{SLUG}/chapters/1/5")
        second = client.get(f"/offline/titles/{SLUG}/chapters/1/5")

    assert first.json() == second.json()
    # The SDK's own disk cache is the only cache: the server asks it every time.
    assert fake.calls == [("get_chapter", 1, "5", None)] * 2
    # One client per request, on the one shared cache_dir - nothing of the app's own.
    assert sdk.call_count == 2
    settings = get_settings()
    for call in sdk.call_args_list:
        assert call.kwargs == {"cache_dir": settings.cache_dir, "cache_ttl": settings.cache_ttl}
    # And nothing for an HTTP cache to keep as a duplicate either: the device stores the
    # fragment on purpose (PR 330), not as a side effect of browsing.
    assert first.headers["cache-control"] == "no-cache"


def test_the_manifest_calls_the_sdk_one_after_another(fake: _FakeClient) -> None:
    response = client.get(f"/offline/titles/{SLUG}/manifest")

    assert response.headers["cache-control"] == "no-cache"
    assert fake.calls == [("get_table_of_contents",), ("estimate_title_size",)]
    assert fake.max_in_flight == 1


def test_a_chapter_is_one_sdk_call() -> None:
    fake = _FakeClient(chapter=_chapter_with_images())
    with patch("app.services.client.RanobeLib", return_value=fake):
        client.get(f"/offline/titles/{SLUG}/chapters/1/5", params={"branch_id": 1})

    # No table of contents, no prefetch of neighbours: the client asks chapter by chapter.
    assert fake.calls == [("get_chapter", 1, "5", 1)]
    assert fake.max_in_flight == 1


# --- PR 331: the reader page as the device keeps it ----------------------------------


def _page(fake: _FakeClient, test_client: TestClient = client) -> str:
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = test_client.get(f"/offline/titles/{SLUG}/chapters/1/2/page")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"
    return response.text


def _chapter_two() -> Chapter:
    return _chapter_with_images().model_copy(update={"number": "2"})


def test_page_is_the_reader_with_its_neighbours_in_sdk_order() -> None:
    fake = _FakeClient(chapter=_chapter_two())
    page = _page(fake)

    assert 'class="reader-page reader-page--offline-copy"' in page
    assert 'data-offline-copy="1"' in page
    assert 'class="reader__title">Глава с картинками</h1>' in page
    # Neighbours exactly as the online reader links them (SDK order: 1/1 ← 1/2 → 2/2.5).
    assert f'href="/titles/{SLUG}/chapters/1/1"' in page
    assert f'href="/titles/{SLUG}/chapters/2/2.5"' in page
    # The same SDK calls as the online reader - and nothing else.
    assert fake.calls == [("get_chapter", 1, "2", None), ("get_table_of_contents",)]


def test_page_points_every_image_at_the_proxy() -> None:
    page = _page(_FakeClient(chapter=_chapter_two()))

    assert f'src="{html.escape(_view(_IMG_A))}"' in page
    assert f'src="{html.escape(_view(_IMG_B))}"' in page
    assert 'src="https://ranobelib.me' not in page
    assert 'src="https://cover.cdnlibs.org' not in page


def test_page_leaves_out_what_needs_the_network() -> None:
    page = _page(_FakeClient(chapter=_chapter_two()))

    assert "Реакции и комментарии появятся, когда будет сеть." in page
    assert "paragraph-menu.js" not in page
    assert "reaction-state.js" not in page
    assert 'data-role="reader-more-trigger"' not in page  # chapter export
    assert "/static/js/offline-store.js" in page
    assert "/static/js/reader-offline.js" in page


def test_page_passes_the_picked_translation_through() -> None:
    fake = _FakeClient(chapter=_chapter_two())
    with patch("app.services.client.RanobeLib", return_value=fake):
        client.get(f"/offline/titles/{SLUG}/chapters/1/2/page", params={"branch_id": 7})

    assert fake.calls[0] == ("get_chapter", 1, "2", 7)


def test_page_for_an_ambiguous_chapter_is_the_json_409() -> None:
    exc = MultipleTranslationsError(SLUG, volume="1", number="2", branches=_BRANCHES)
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(exc=exc)):
        response = client.get(
            f"/offline/titles/{SLUG}/chapters/1/2/page", headers={"accept": "text/html"}
        )

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/json"


def test_the_online_reader_keeps_its_live_features() -> None:
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter=_chapter_two())):
        page = client.get(f"/titles/{SLUG}/chapters/1/2").text

    assert "reader-page--offline-copy" not in page
    assert "data-offline-copy" not in page
    assert "paragraph-menu.js" in page
    assert "Реакции и комментарии появятся" not in page
    # Notes the last chapter opened, for the offline page's «Продолжить».
    assert "/static/js/reader-offline.js" in page


@pytest.fixture
def signed_in(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        register(test_client, "alice@example.com", nickname="alice")
        yield test_client
    get_settings.cache_clear()


async def test_a_signed_in_copy_is_nobodys_page_and_records_nothing(
    signed_in: TestClient,
) -> None:
    page = _page(_FakeClient(chapter=_chapter_two()), signed_in)

    # Nothing personal in what the device keeps...
    assert 'data-authenticated=""' in page
    assert 'data-user-id=""' in page
    assert "alice" not in page
    assert "reading-progress-tick.js" not in page
    assert "activity-heartbeat.js" not in page
    # ...and downloading isn't reading: unlike the reader route, no library entry and no
    # «read today» record.
    async with connection() as conn:
        assert await list_entries(conn, user_id=1) == []
        assert await list_chapters_read_today(conn, user_id=1) == []

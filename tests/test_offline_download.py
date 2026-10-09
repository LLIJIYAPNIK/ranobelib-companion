"""PR 330: «Скачать для чтения без интернета» - the download queue, the offline index's
schema migrations, and the markup the title page and /downloads hand to the scripts.

The queue (app/static/js/offline-queue.js) and the migrations (offline-store.js) run
under Node (node:vm - see tests/js/offline_queue_harness.mjs); those tests are skipped
where Node isn't installed.
"""

import json
import re
import shutil
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib.models import (
    Chapter,
    ChapterBranch,
    ChapterUser,
    Cover,
    Label,
    Title,
    Volume,
)

from app.config import get_settings
from app.main import app
from app.static_assets import asset_hash
from tests.auth_helpers import register
from tests.db_reset import reset_app_database

_ROOT = Path(__file__).resolve().parent.parent
_JS = _ROOT / "app" / "static" / "js"
_HARNESS = _ROOT / "tests" / "js" / "offline_queue_harness.mjs"

SLUG = "6712--test-novel"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


@cache
def _scenarios() -> dict[str, Any]:
    completed = subprocess.run(
        ["node", str(_HARNESS), str(_JS / "offline-queue.js"), str(_JS / "offline-store.js")],
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(completed.stdout)


# --- the queue: strictly one chapter at a time ---------------------------------------


@needs_node
def test_chapters_are_fetched_one_after_another() -> None:
    sequential = _scenarios()["sequential"]

    # The second chapter isn't even started until the first is done.
    assert sequential["afterStart"] == ["1"]
    assert sequential["started"] == ["1", "2", "3"]
    assert sequential["maxInFlight"] == 1
    assert (sequential["state"], sequential["completed"], sequential["bytes"]) == ("done", 3, 300)


@needs_node
@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        ("rateLimit", "rate-limit"),
        ("blocked", "blocked"),
        ("quota", "quota"),
        ("offline", "offline"),
    ],
)
def test_pushback_pauses_in_place_and_resume_redoes_that_chapter(
    scenario: str, reason: str
) -> None:
    paused = _scenarios()[scenario]["paused"]
    after = _scenarios()[scenario]["after"]

    # 429 / 503 / no space / no network: stop asking, keep the place.
    assert (paused["state"], paused["pauseReason"]) == ("paused", reason)
    assert (paused["position"], paused["completed"]) == (1, 1)
    assert paused["failed"] == []
    # «Продолжить» fetches chapter 2 again, then carries on - still one at a time.
    assert after["started"] == ["1", "2", "2", "3"]
    assert (after["state"], after["completed"]) == ("done", 3)
    assert after["maxInFlight"] == 1


@needs_node
def test_other_failures_skip_the_chapter_and_retry_runs_only_those() -> None:
    done = _scenarios()["failedThenRetried"]["done"]
    after = _scenarios()["failedThenRetried"]["after"]

    assert done["state"] == "done"
    assert done["failed"] == [["1", "Глава не найдена"]]
    assert done["completed"] == 2
    # «Повторить»: just chapter 1 again, not the whole selection.
    assert after["started"] == ["1", "2", "3", "1"]
    assert (after["completed"], after["failed"]) == (3, [])


@needs_node
def test_pause_and_instant_resume_never_overlap() -> None:
    result = _scenarios()["pauseResume"]

    # The aborted attempt has to settle before the next run asks for anything.
    assert result["aborted"] == ["1"]
    assert result["started"] == ["1", "1", "2"]
    assert result["maxInFlight"] == 1
    assert (result["state"], result["completed"]) == ("done", 2)


@needs_node
def test_cancel_aborts_and_stays_cancelled() -> None:
    cancelled = _scenarios()["cancel"]["cancelled"]
    restarted = _scenarios()["cancel"]["restarted"]

    assert cancelled["state"] == "cancelled"
    assert cancelled["aborted"] == ["2"]
    assert cancelled["completed"] == 1
    assert restarted["started"] == ["1", "2"]


# --- the offline index: schema migrations --------------------------------------------


@needs_node
def test_a_fresh_device_gets_the_whole_schema() -> None:
    migrations = _scenarios()["migrations"]

    assert migrations["version"] == migrations["steps"] == 1
    assert migrations["fresh"] == {
        "titles": {"keyPath": "slug", "indexes": {}},
        "chapters": {"keyPath": ["slug", "volume", "number"], "indexes": {"slug": "slug"}},
    }


@needs_node
def test_reopening_at_the_current_version_changes_nothing() -> None:
    migrations = _scenarios()["migrations"]

    # upgrade(db, DB_VERSION) runs no step - a store created twice would throw.
    assert migrations["reopenedUnchanged"] == migrations["fresh"]


@needs_node
def test_store_urls_and_feature_detection() -> None:
    migrations = _scenarios()["migrations"]

    assert migrations["supportedWithoutApis"] is False
    # The PR 329 fragment URL, without a query: what the copy is stored under.
    assert migrations["chapterUrl"] == f"/offline/titles/{SLUG}/chapters/1/2.5"
    assert migrations["coverUrl"] == (
        "/images/view?url=https%3A%2F%2Fcover.cdnlibs.org%2Fa%20b.jpg"
    )


# --- the title page ------------------------------------------------------------------


def _branch(branch_id: int) -> ChapterBranch:
    return ChapterBranch(
        id=branch_id,
        branch_id=branch_id,
        created_at=datetime(2024, 1, branch_id, tzinfo=UTC),
        user=ChapterUser(id=branch_id, username="u"),
    )


_VOLUMES = [
    Volume(
        number="1",
        chapters=[
            Chapter(id=n, volume="1", number=str(n), branches=[_branch(1)]) for n in (1, 2, 3)
        ]
        + [
            Chapter(
                id=4, volume="1", number="4", branches=[_branch(1), _branch(2)], branches_count=2
            )
        ],
    )
]


class _FakeClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_info(self) -> Title:
        return Title(
            id=6712,
            name="Test Novel",
            rus_name="Тестовый роман",
            slug="test-novel",
            slug_url=SLUG,
            cover=Cover(default="https://cover.cdnlibs.org/c.jpg"),
            age_restriction=Label(id=0, label="16+"),
            status=Label(id=1, label="Онгоинг"),
        )

    async def get_table_of_contents(self) -> list[Volume]:
        return _VOLUMES

    async def get_chapter(self, volume: int, number: str, *, branch_id: int | None = None):
        return Chapter(id=3, volume="1", number=number, content="<p>Текст</p>")


@pytest.fixture
def sdk() -> Iterator[None]:
    with patch("app.services.client.RanobeLib", _FakeClient):
        yield


def _manager(html: str) -> str:
    match = re.search(r'<div\s+class="wn-offline"[^>]*>', html)
    assert match, "no offline manager in the title fragment"
    return match.group(0)


def test_guest_title_page_gets_the_manager_and_its_triggers(sdk: None) -> None:
    html = TestClient(app).get(f"/titles/{SLUG}/data").text

    manager = _manager(html)
    assert f'data-slug-url="{SLUG}"' in manager
    assert 'data-title-name="Тестовый роман"' in manager
    assert 'data-cover-url="https://cover.cdnlibs.org/c.jpg"' in manager
    # No reading position, no saved translation: from the first chapter, and asked.
    assert 'data-current-index=""' in manager
    assert 'data-default-translation=""' in manager
    assert "hidden" in manager
    # Both entry points start hidden - the script shows them where storage works.
    assert len(re.findall(r'data-role="offline-download-open"[^>]*hidden', html)) == 2
    assert 'data-role="offline-download-status" hidden' in html
    # «Вариант N» is chosen by the visitor: the select starts on an empty choice.
    assert '<option value="">Выберите вариант</option>' in html


def test_title_page_loads_the_offline_scripts_before_the_content_loader(sdk: None) -> None:
    html = TestClient(app).get(f"/titles/{SLUG}").text

    order = [
        html.index(f"/static/js/{name}?v={asset_hash('js/' + name)}")
        for name in (
            "offline-store.js",
            "offline-queue.js",
            "offline-download.js",
            "title-content-load.js",
        )
    ]
    assert order == sorted(order)
    loader = (_JS / "title-content-load.js").read_text(encoding="utf-8")
    assert "window.initOfflineDownload()" in loader


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch, sdk: None) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        register(test_client, "alice@example.com")
        yield test_client
    get_settings.cache_clear()


def test_the_default_starts_at_the_reading_position_with_the_saved_translation(
    reader: TestClient,
) -> None:
    # Reading chapter 3 (index 2) puts the title in the library at that position.
    assert reader.get(f"/titles/{SLUG}/chapters/1/3").status_code == 200
    saved = reader.post(
        f"/library/{SLUG}/default-translation",
        data={"translation_index": "1"},
        follow_redirects=False,
    )
    assert saved.status_code in (200, 303)

    manager = _manager(reader.get(f"/titles/{SLUG}/data").text)

    assert 'data-current-index="2"' in manager
    assert 'data-default-translation="1"' in manager


# --- «Скачано» on /downloads ---------------------------------------------------------


def test_guests_get_the_saved_section_too() -> None:
    html = TestClient(app).get("/downloads").text

    # The copy belongs to the device, not an account: listed even behind the lock.
    assert 'data-role="offline-saved"' in html
    assert re.search(r'<section[^>]*id="offline"[^>]*hidden', html)
    assert f"/static/js/offline-saved.js?v={asset_hash('js/offline-saved.js')}" in html
    assert html.index("js/offline-store.js") < html.index("js/offline-saved.js")


def test_signed_in_downloads_page_has_one_saved_section(reader: TestClient) -> None:
    html = reader.get("/downloads").text

    assert html.count('data-role="offline-saved"') == 1
    assert 'data-bottom-sheet-title="Удалить скачанное?"' in html


def test_saved_section_has_the_storage_warning_and_clear_all() -> None:
    # PR 333: a warning past 80% of the browser's share, and every download at once.
    html = TestClient(app).get("/downloads").text

    assert re.search(r'data-role="offline-saved-quota-warning"[^>]*hidden', html)
    assert 'data-role="offline-clear-all"' in html
    assert "Очистить офлайн-данные" in html
    assert 'data-bottom-sheet-title="Очистить офлайн-данные?"' in html

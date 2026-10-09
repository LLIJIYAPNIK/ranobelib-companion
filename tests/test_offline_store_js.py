"""PR 335: sizes, the limit and the cleanup of read chapters in
app/static/js/offline-store.js.

Run under Node over an in-memory IndexedDB and Cache Storage - see
tests/js/offline_store_harness.mjs. The title there has chapters 1-10 of volume 1
(chapter n is 1000 + n bytes of page plus a 1 KB image; chapters 2 and 9 share one more),
and another title has one chapter that reuses chapter 3's image. Skipped where Node isn't
installed.
"""

import json
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HARNESS = _ROOT / "tests" / "js" / "offline_store_harness.mjs"
_STORE = _ROOT / "app" / "static" / "js" / "offline-store.js"
SLUG = "6712--test-novel"
OTHER = "9001--other-novel"
KB = 1024

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


@cache
def _results() -> dict[str, Any]:
    completed = subprocess.run(
        ["node", str(_HARNESS), str(_STORE)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(completed.stdout)


def _chapter_bytes(n: int, *, shared: bool = False) -> int:
    return 1000 + n + KB + (KB if shared else 0)


def test_sizes_add_up_per_title() -> None:
    sizes = _results()["sizes"]

    title = sum(_chapter_bytes(n, shared=n in (2, 9)) for n in range(1, 11))
    assert sizes[SLUG] == {"chapters": 10, "bytes": title}
    assert sizes[OTHER] == {"chapters": 1, "bytes": 500 + KB}


def test_the_limit_counts_the_downloads_own_bytes() -> None:
    results = _results()

    assert results["limitOff"] is None  # no limit by default
    used = results["sizes"][SLUG]["bytes"] + results["sizes"][OTHER]["bytes"]
    assert results["limit100"] == {
        "used": used,
        "limit": 100 * KB * KB,
        "percent": 0,
        "reached": False,
    }
    assert results["limitGarbage"] is None  # not one of the choices: no limit
    reached = results["limit100Reached"]
    assert reached["used"] == used + 100 * KB * KB
    assert reached["reached"] is True
    assert reached["percent"] == 100


def test_read_is_behind_the_current_chapter_minus_what_is_kept() -> None:
    read = _results()["readKeys"]

    assert read["current6keep0"] == ["1--1", "1--2", "1--3", "1--4", "1--5"]
    assert read["current6keep2"] == ["1--1", "1--2", "1--3"]
    assert read["current2keep5"] == []
    assert read["first"] == []
    # Not in the kept order, or no order at all: no telling what's behind it.
    assert read["notInToc"] == []
    assert read["noToc"] == []


def test_cleanup_never_touches_the_current_or_unread_chapters() -> None:
    cleanup = _results()["cleanup"]

    assert cleanup["left"] == [4, 5, 6, 7, 8, 9, 10]
    assert cleanup["freed"] == {
        "chapters": 3,
        "bytes": sum(_chapter_bytes(n, shared=n == 2) for n in (1, 2, 3)),
    }
    assert cleanup["other"] == [1]
    assert cleanup["titleKept"] is True
    assert cleanup["coverKept"] is True


def test_cleanup_keeps_images_another_chapter_still_uses() -> None:
    removed = _results()["cleanup"]["removedFromCache"]

    assert removed == [
        "/img/1",
        "/img/2",
        f"/titles/{SLUG}/chapters/1/1",
        f"/titles/{SLUG}/chapters/1/2",
        f"/titles/{SLUG}/chapters/1/3",
    ]
    # /img/shared (chapter 9) and /img/3 (the other title) stay.


def test_remove_read_starts_from_the_chapter_opened_last() -> None:
    results = _results()

    # Nothing opened on this device: nothing counts as read.
    assert results["noneOpened"] == {"chapters": 0, "bytes": 0}
    opened = results["lastOpened"]
    assert opened["read"] == [1, 2, 3]
    assert opened["freed"]["chapters"] == 3
    assert opened["left"] == [4, 5, 6, 7, 8, 9, 10]
    assert results["lastOpenedAgain"] == {"chapters": 0, "bytes": 0}


def test_deleting_a_title_takes_its_cover_and_keeps_shared_images() -> None:
    deleted = _results()["deleteTitle"]

    assert deleted["titles"] == [OTHER]
    assert deleted["cache"] == ["/img/3", f"/titles/{OTHER}/chapters/1/1"]


def test_the_last_opened_date_is_for_downloaded_titles_and_can_be_forgotten() -> None:
    opened = _results()["opened"]

    assert opened["opened"] == {SLUG: "2026-10-01T10:00:00.000Z", OTHER: None}
    assert opened["forgotten"] == {SLUG: None, OTHER: None}
    assert opened["titles"] == 2  # forgetting a date keeps the downloads


def test_downloading_more_keeps_when_and_where_the_title_was_opened() -> None:
    """PR 339's fix: saveChapter() used to replace the title record, dropping the
    last-opened date (PR 335) and chapter (PR 336) - auto-download did it on every open."""
    kept = _results()["saveKeepsOpened"]

    assert kept == {
        "openedAt": "2026-10-01T10:00:00.000Z",
        "openedChapter": {"volume": "1", "number": "4"},
        "chapters": 11,
    }


def test_a_refreshed_copy_drops_only_images_nothing_uses() -> None:
    dropped = _results()["dropUnused"]

    assert dropped["before"] is True
    assert "/img/gone" not in dropped["after"]
    assert {"/img/shared", "/img/3"} <= set(dropped["after"])


def test_the_open_from_copy_setting_reaches_the_cache_only_while_off() -> None:
    steps = _results()["preferences"]

    assert steps["defaultOn"] is True
    assert steps["nothingWritten"] is False  # on, nothing downloaded: no cache made for it
    assert steps["readOff"] is False
    assert steps["off"] == {"openDownloadedFromCopy": False}
    assert steps["offAgain"] == {"openDownloadedFromCopy": False}
    assert steps["onAgain"] is None
    assert steps["garbage"] is True


def test_offline_settings_default_off_and_take_only_the_choices() -> None:
    settings = _results()["settings"]

    assert settings["defaults"] == {"cleanBehind": 0, "limitMb": 0}
    assert settings["saved"] == {"cleanBehind": 10, "limitMb": 250}
    assert settings["coerced"] == {"cleanBehind": 0, "limitMb": 500}
    assert settings["garbage"] == {"cleanBehind": 0, "limitMb": 0}


def test_the_chapter_opened_is_kept_with_the_date_and_forgotten_with_it() -> None:
    """PR 336: where /continue leads without a network."""
    opened = _results()["openedChapter"]

    assert opened["first"] == {SLUG: {"volume": "1", "number": "4"}, OTHER: None}
    assert opened["second"] == {SLUG: {"volume": "1", "number": "5"}, OTHER: None}
    assert opened["forgotten"] == {SLUG: None, OTHER: None}

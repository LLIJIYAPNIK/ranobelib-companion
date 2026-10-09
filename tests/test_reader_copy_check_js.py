"""PR 339: a downloaded copy shown while online checks itself against the site
(app/static/js/reader-copy-check.js) and offers «Обновить копию».

Run under Node with a fake version endpoint, store and queue - see
tests/js/reader_copy_check_harness.mjs. Skipped where Node isn't installed.
"""

import json
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HARNESS = _ROOT / "tests" / "js" / "reader_copy_check_harness.mjs"
_SCRIPT = _ROOT / "app" / "static" / "js" / "reader-copy-check.js"
VERSION_URL = "/offline/titles/6712--test-novel/chapters/1/3/version"
UPDATED = "Глава обновлена на сайте."

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


@cache
def _results() -> dict[str, Any]:
    completed = subprocess.run(
        ["node", str(_HARNESS), str(_SCRIPT)],
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(completed.stdout)


def test_an_unchanged_chapter_shows_nothing() -> None:
    same = _results()["same"]

    assert same["shown"] is False
    assert same["log"] == [f"fetch {VERSION_URL}"]


def test_a_changed_chapter_says_so() -> None:
    updated = _results()["updated"]

    assert updated["shown"] is True
    assert updated["text"] == UPDATED


def test_the_check_asks_about_the_translation_the_copy_is_of() -> None:
    assert _results()["branch"]["log"] == [f"fetch {VERSION_URL}?branch_id=7"]


@pytest.mark.parametrize("case", ["serverError", "networkFails"])
def test_a_failed_check_shows_nothing(case: str) -> None:
    assert _results()[case]["shown"] is False


@pytest.mark.parametrize("case", ["deviceOffline", "oldCopy", "onlineReader"])
def test_nothing_is_asked_when_there_is_nothing_to_compare(case: str) -> None:
    # No network; a copy downloaded before PR 339 (no version); the online reader.
    result = _results()[case]

    assert result == {"shown": False, "text": UPDATED, "disabled": False, "log": []}


def test_refresh_downloads_the_chapter_again_then_reloads() -> None:
    refresh = _results()["refresh"]

    assert refresh["log"] == [
        f"fetch {VERSION_URL}",
        'download 6712--test-novel {"volume":"1","number":"3","branchId":null} true',
        # The old copy's images - the store keeps those another chapter still uses.
        "drop /img/old,/img/kept",
        "reload",
    ]
    assert refresh["disabled"] is True
    assert refresh["text"] == "Обновляем копию…"


def test_a_failed_refresh_keeps_the_copy_and_can_be_tried_again() -> None:
    failed = _results()["refreshFails"]

    assert "reload" not in failed["log"]
    assert not any(entry.startswith("drop") for entry in failed["log"])
    assert failed["disabled"] is False
    assert failed["text"] == "Не удалось обновить копию — попробуйте позже."

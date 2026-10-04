"""Infinite scroll of the catalog - app/static/js/catalog-scroll.js (PR 295: feed
cursor, loading status, the «Повторить» plate), run under Node with a fake grid,
IntersectionObserver and fetch (tests/js/catalog_scroll_harness.mjs). Skipped where
Node isn't installed.
"""

import json
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "app" / "static" / "js" / "catalog-scroll.js"
_HARNESS = _ROOT / "tests" / "js" / "catalog_scroll_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


@cache
def _scenarios() -> dict[str, dict[str, Any]]:
    completed = subprocess.run(
        ["node", str(_HARNESS), str(_SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(completed.stdout)


def test_trigger_zone_is_800px() -> None:
    assert _scenarios()["success"]["rootMargin"] == "800px 0px"


def test_pages_carry_the_feed_cursor_and_take_the_next_one() -> None:
    result = _scenarios()["success"]
    first, second = result["fetched"]
    assert "page=2" in first and "shown=30" in first and "featured=2" in first
    assert "page=3" in second and "shown=60" in second and "featured=5" in second
    assert result["cursor"]["shown"] == "90"
    assert result["cursor"]["featured"] == "7"
    assert result["appended"] == 2


def test_loading_status_while_a_page_loads_and_cleared_after() -> None:
    result = _scenarios()["success"]
    assert result["loadingSeen"] == ["Загружаем ещё…", "Загружаем ещё…"]
    assert result["log"][-1]["status"] == ""
    assert result["log"][-1]["busy"] is None


def test_last_page_stops_observing_and_shows_the_end() -> None:
    assert _scenarios()["success"]["observing"] is False
    assert _scenarios()["success"]["endShown"] is True


def test_no_end_after_a_failure() -> None:
    assert _scenarios()["networkFailureThenRetry"]["endShown"] is False
    assert _scenarios()["serverError"]["endShown"] is False


def test_failure_shows_the_retry_plate_and_pauses_loading() -> None:
    result = _scenarios()["networkFailureThenRetry"]
    after_fail, after_scroll, after_retry = result["log"]
    assert after_fail["error"] and after_fail["retry"]
    # Scrolling while the plate is up doesn't silently try again.
    assert after_scroll["error"]
    assert len(result["fetched"]) == 2  # the failed one + the retry, nothing in between
    # «Повторить» asks for the same page with the same cursor, and clears the plate.
    assert result["fetched"][0] == result["fetched"][1]
    assert after_retry["error"] is False
    assert result["appended"] == 1
    assert result["observing"] is True


def test_a_server_error_shows_the_plate_too() -> None:
    result = _scenarios()["serverError"]
    assert result["log"][0]["error"] is True
    assert result["appended"] == 0


def test_pages_carry_the_status_and_chapter_count_filters() -> None:
    # PR 303: data-statuses / data-min-chapters off the grid, like genres/countries.
    (url,) = _scenarios()["filters"]["fetched"]
    assert "statuses=1&statuses=2" in url
    assert "min_chapters=100" in url

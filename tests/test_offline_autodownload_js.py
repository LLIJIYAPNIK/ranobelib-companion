"""Auto-download of the next chapters, app/static/js/offline-autodownload.js (PR 334).

Run under Node with the real queue under it (offline-queue.js) and fake store, network
and storage - see tests/js/offline_autodownload_harness.mjs. The current chapter is 5 of
12, chapters 1-5 are on the device and chapter 8 has two translations, unless a scenario
says otherwise. Skipped where Node isn't installed.
"""

import json
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_JS = _ROOT / "app" / "static" / "js"
_HARNESS = _ROOT / "tests" / "js" / "offline_autodownload_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


@cache
def _scenarios() -> dict[str, Any]:
    completed = subprocess.run(
        [
            "node",
            str(_HARNESS),
            str(_JS / "offline-queue.js"),
            str(_JS / "offline-autodownload.js"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(completed.stdout)


def _numbers(name: str) -> list[str]:
    return [download["number"] for download in _scenarios()[name]["downloads"]]


def test_off_by_default_does_nothing() -> None:
    off = _scenarios()["off"]

    assert off["result"] == {"skipped": "off"}
    assert off["fetched"] == []


def test_on_wifi_the_next_chapters_come_one_at_a_time() -> None:
    wifi = _scenarios()["wifi"]

    # 6 and 7; 8 has two translations and no «Вариант N» was ever chosen - skipped.
    assert _numbers("wifi") == ["6", "7"]
    assert wifi["maxInFlight"] == 1
    assert wifi["fetched"] == ["/offline/titles/6712--test-novel/manifest?size=false"]
    assert _numbers("ethernet") == ["6", "7"]


def test_ten_ahead_is_one_after_another_too() -> None:
    assert _numbers("ten") == ["2", "3", "4", "5", "6", "7", "9", "10", "11"]
    assert _scenarios()["ten"]["maxInFlight"] == 1


def test_never_with_save_data_or_on_mobile_data() -> None:
    for name, reason in [("saveData", "save-data"), ("cellular", "cellular")]:
        scenario = _scenarios()[name]
        assert scenario["result"] == {"skipped": reason}
        assert scenario["fetched"] == []
        assert scenario["downloads"] == []


def test_an_unknown_network_needs_the_visitors_yes() -> None:
    assert _scenarios()["unknownNotAllowed"]["result"] == {"skipped": "unknown"}
    assert _scenarios()["unknownNotAllowed"]["fetched"] == []
    assert _numbers("unknownAllowed") == ["6", "7"]


def test_titles_outside_the_downloaded_set_dont_download_themselves() -> None:
    not_in_set = _scenarios()["notInSet"]

    assert not_in_set["result"] == {"skipped": "not-downloaded"}
    assert not_in_set["fetched"] == []


def test_whats_already_on_the_device_isnt_downloaded_again() -> None:
    # Everything ahead is there: not even the manifest is asked for.
    ahead = _scenarios()["alreadyAhead"]
    assert ahead["result"] == {"skipped": "up-to-date"}
    assert ahead["fetched"] == []
    # 6 is there already: only 7.
    assert _numbers("partlyAhead") == ["7"]


def test_several_translations_follow_the_visitors_variant_or_are_skipped() -> None:
    chosen = _scenarios()["variantChosen"]["downloads"]
    assert [(d["number"], d["branchId"]) for d in chosen] == [("7", None), ("8", 102), ("9", None)]
    assert _numbers("variantUnknown") == ["7", "9"]


@pytest.mark.parametrize(
    ("name", "reason", "attempted", "cooldown_until"),
    [
        ("rateLimited", "rate-limit", ["6", "7"], 1_000_000 + 15 * 60 * 1000),
        ("blocked", "blocked", ["6"], 1_000_000 + 30 * 60 * 1000),
        ("quota", "quota", ["6"], 1_000_000 + 60 * 60 * 1000),
    ],
)
def test_pushback_or_a_full_device_stops_it_and_holds_off(
    name: str, reason: str, attempted: list[str], cooldown_until: int
) -> None:
    scenario = _scenarios()[name]

    assert _numbers(name) == attempted  # nothing after the chapter that was refused
    assert scenario["result"]["pauseReason"] == reason
    assert scenario["result"]["state"] == "cancelled"
    assert scenario["cooldownUntil"] == cooldown_until


def test_while_holding_off_nothing_is_asked() -> None:
    cooling = _scenarios()["coolingDown"]

    assert cooling["result"] == {"skipped": "cooldown"}
    assert cooling["fetched"] == []
    assert _numbers("cooldownOver") == ["6", "7"]


def test_a_nearly_full_storage_doesnt_start_it() -> None:
    nearly_full = _scenarios()["nearlyFull"]

    assert nearly_full["result"] == {"skipped": "storage"}
    assert nearly_full["fetched"] == []


def test_the_offline_limit_doesnt_start_it_and_stops_it_where_its_reached() -> None:
    # PR 335: the limit set in «Офлайн» - checked before every chapter, no cooldown (it's
    # a local check: freeing room lets the next chapter page go on).
    reached = _scenarios()["limitReached"]
    assert reached["result"] == {"skipped": "limit"}
    assert reached["fetched"] == []

    midway = _scenarios()["limitMidway"]
    assert _numbers("limitMidway") == ["6"]
    assert midway["result"]["pauseReason"] == "limit"
    assert midway["result"]["state"] == "cancelled"
    assert midway["cooldownUntil"] is None

    assert _numbers("limitFar") == ["6", "7"]


def test_another_failure_skips_that_chapter_only() -> None:
    assert _numbers("otherFailure") == ["6", "7"]
    assert _scenarios()["otherFailure"]["result"]["downloaded"] == 1


def test_network_statuses() -> None:
    statuses = _scenarios()["statuses"]

    assert statuses["wifi"] == {"ok": True, "reason": "wifi"}
    assert statuses["cellular"] == {"ok": False, "reason": "cellular"}  # even if allowed
    assert statuses["unknownType"] == {"ok": False, "reason": "unknown"}
    assert statuses["noApi"] == {"ok": False, "reason": "unknown"}
    assert statuses["noApiAllowed"] == {"ok": True, "reason": "any"}
    assert statuses["saveDataAllowed"] == {"ok": False, "reason": "save-data"}

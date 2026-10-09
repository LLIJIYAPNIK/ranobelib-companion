"""The device's queue for reading progress and activity, app/static/js/sync-queue.js (PR 332).

Run under Node (node:vm with a fake IndexedDB, clock and network - see
tests/js/sync_queue_harness.mjs), the same way as the other small standalone scripts: what
matters is what goes out when, and what stays on the device - offline, back online, with a
lost answer. The server half (a resent heartbeat isn't counted twice, a late tick doesn't
undo newer reading) is in test_api_activity_heartbeat.py/test_api_reading_progress.py;
Background Sync in the service worker is in test_service_worker.py. Skipped where Node
isn't installed.
"""

import json
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "app" / "static" / "js" / "sync-queue.js"
_HARNESS = _ROOT / "tests" / "js" / "sync_queue_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


@cache
def _scenarios() -> dict[str, Any]:
    completed = subprocess.run(
        ["node", str(_HARNESS), str(_SCRIPT)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(completed.stdout)


def test_online_an_event_goes_out_at_once_and_leaves_the_queue() -> None:
    online = _scenarios()["online"]

    (request,) = online["requests"]
    assert request["url"] == "/activity/heartbeat"
    assert request["seconds"] == "30"
    assert request["event_id"] == "uuid-0001"
    assert request["age_ms"] == "0"
    # Survives the page being left; a signed-out 303 isn't followed to /login's 200.
    assert request["keepalive"] is True
    assert request["redirect"] == "manual"
    assert online["left"] == []
    assert online["syncRegistrations"] == []


def test_offline_events_wait_on_the_device_and_ask_for_background_sync() -> None:
    while_offline = _scenarios()["offlineThenOnline"]["whileOffline"]

    assert while_offline["requests"] == 0
    assert while_offline["left"] == 2
    assert set(while_offline["syncRegistrations"]) == {"wn-sync"}


def test_back_online_the_queue_goes_out_oldest_first_dated_by_its_wait() -> None:
    scenario = _scenarios()["offlineThenOnline"]

    assert [(r["event_id"], r["age_ms"]) for r in scenario["requests"]] == [
        ("uuid-0001", "90000"),
        ("uuid-0002", "60000"),
    ]
    assert scenario["left"] == []


def test_a_failed_attempt_is_resent_with_the_same_id() -> None:
    # The server ignores the second copy of an id it has (activity_events.event_id), so a
    # lost answer can't count the seconds twice.
    scenario = _scenarios()["droppedMidChapter"]

    assert [r["event_id"] for r in scenario["requests"]] == ["uuid-0001", "uuid-0001"]
    assert scenario["left"] == []
    assert scenario["syncRegistrations"] == ["wn-sync"]


def test_queued_ticks_of_a_chapter_collapse_into_the_latest() -> None:
    scenario = _scenarios()["ticksCollapse"]

    assert scenario["queued"] == 2
    assert [(r["number"], r["paragraph"]) for r in scenario["requests"]] == [
        ("6", "3"),
        ("5", "30"),
    ]
    assert "event_id" not in scenario["requests"][0]  # a position, not a sum - no id needed
    assert scenario["left"] == []


def test_final_answers_drop_an_event_and_a_busy_server_keeps_the_rest() -> None:
    # 1: signed out (303 → opaque redirect), 2: 422 - final, dropped. 3: 503 - stop there,
    # keep it and everything after it for later.
    answers = _scenarios()["answers"]

    assert answers["sent"] == ["1", "2", "3"]
    assert answers["left"] == ["3", "4"]
    assert "wn-sync" in answers["syncRegistrations"]


def test_what_an_earlier_page_left_goes_out_when_the_next_one_loads() -> None:
    on_load = _scenarios()["onLoad"]

    assert [(r["event_id"], r["age_ms"]) for r in on_load["requests"]] == [("uuid-left", "100000")]
    assert on_load["left"] == []


def test_nothing_is_sent_before_the_account_check_and_a_cleared_queue_sends_nothing() -> None:
    # PR 333: another account's leftovers are cleared by device-account.js first - they
    # must never go out under this page's session.
    assert _scenarios()["accountSwitch"] == {"beforeReady": 0, "requests": 0, "left": 0}


def test_an_answer_for_an_older_tick_doesnt_remove_a_newer_one() -> None:
    assert _scenarios()["newerKept"]["left"] == ["20"]


def test_two_triggers_at_once_send_each_event_once() -> None:
    assert _scenarios()["oneFlushAtATime"] == {"requests": 2, "left": 0}


def test_without_indexeddb_events_are_still_sent() -> None:
    assert _scenarios()["noIndexedDB"]["requests"] == 1

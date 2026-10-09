"""Throttling of app/static/js/reading-progress-tick.js (PR 288).

The script is run under Node (node:vm, fake DOM and clock - see
tests/js/reading_progress_tick_harness.mjs) rather than a browser: it only touches a
handful of DOM calls, and what matters here is timing - how many POST
/reading-progress/tick requests a run of taps turns into. Skipped where Node isn't
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
_SCRIPT = _ROOT / "app" / "static" / "js" / "reading-progress-tick.js"
_HARNESS = _ROOT / "tests" / "js" / "reading_progress_tick_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


@cache
def _scenarios() -> dict[str, list[dict[str, Any]]]:
    completed = subprocess.run(
        ["node", str(_HARNESS), str(_SCRIPT)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(completed.stdout)


def _sent(scenario: str) -> list[tuple[int, str]]:
    return [(request["at"], request["paragraph"]) for request in _scenarios()[scenario]]


def test_a_quick_run_of_taps_is_not_one_request_per_tap() -> None:
    # Ten taps 100 ms apart: the first at once, the rest as one trailing request
    # carrying the latest position.
    assert _sent("quickTaps") == [(0, "2"), (5000, "11")]


def test_taps_further_apart_than_the_window_each_go_out() -> None:
    assert _sent("slowTaps") == [(0, "2"), (6000, "3"), (12000, "4")]


def test_the_request_carries_the_whole_position() -> None:
    request = _scenarios()["quickTaps"][0]

    assert request["url"] == "/reading-progress/tick"
    assert request["slug_url"] == "6712--test-novel"
    assert request["volume"] == "1"
    assert request["number"] == "5"
    assert request["paragraph_total"] == "80"
    # PR 332: queued ticks of one chapter collapse into the latest.
    assert request["key"] == "tick:6712--test-novel:1:5"


def test_the_position_the_server_already_holds_is_not_resent() -> None:
    assert _sent("alreadyOnServer") == []


@pytest.mark.parametrize("scenario", ["flushOnHide", "flushOnPagehide"])
def test_a_pending_position_is_flushed_when_the_page_goes_away(scenario: str) -> None:
    assert _sent(scenario) == [(0, "2"), (1500, "3")]


def test_impossible_positions_are_not_sent() -> None:
    assert _sent("invalid") == []

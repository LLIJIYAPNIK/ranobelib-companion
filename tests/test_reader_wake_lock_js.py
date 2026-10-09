"""PR 338: «Не гасить экран при чтении» (app/static/js/reader-wake-lock.js).

The module runs under Node with a fake screen Wake Lock API - see
tests/js/reader_wake_lock_harness.mjs; those tests are skipped where Node isn't installed.
The setting's switches and script includes are checked in the templates.
"""

import json
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HARNESS = _ROOT / "tests" / "js" / "reader_wake_lock_harness.mjs"
_SCRIPT = _ROOT / "app" / "static" / "js" / "reader-wake-lock.js"
_TEMPLATES = _ROOT / "app" / "templates"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


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


def _state(log: list[str], held: bool) -> dict[str, Any]:
    return {"log": log, "held": held}


@needs_node
def test_off_by_default_nothing_is_requested() -> None:
    assert _results()["off"] == _state([], False)


@needs_node
def test_taken_again_once_the_tab_is_visible_again() -> None:
    reacquire = _results()["reacquire"]

    assert reacquire["atLoad"] == _state(["request:screen"], True)
    assert reacquire["hidden"] == _state(["request:screen"], False)  # the system let go
    # Two visibilitychange events, one request.
    assert reacquire["visibleAgain"] == _state(["request:screen", "request:screen"], True)


@needs_node
def test_a_lock_dropped_without_an_event_is_taken_again() -> None:
    assert _results()["silentDrop"] == _state(["request:screen", "request:screen"], True)


@needs_node
def test_a_background_tab_waits_until_it_is_visible() -> None:
    background = _results()["background"]

    assert background["background"] == _state([], False)
    assert background["visible"] == _state(["request:screen"], True)


@needs_node
def test_the_switch_takes_and_lets_go() -> None:
    toggle = _results()["toggle"]

    assert toggle["on"] == _state(["request:screen"], True)
    assert toggle["otherKey"] == _state(["request:screen"], True)
    assert toggle["off"] == _state(["request:screen", "release"], False)
    assert toggle["reset"] == _state(["request:screen", "release", "request:screen"], True)


@needs_node
def test_another_tab_turning_it_on_and_off() -> None:
    other_tab = _results()["otherTab"]

    assert other_tab["on"] == _state(["request:screen"], True)
    assert other_tab["off"] == _state(["request:screen", "release"], False)


@needs_node
def test_leaving_the_reader_lets_go_and_coming_back_takes_it_again() -> None:
    leave = _results()["leave"]

    assert leave["left"] == _state(["request:screen", "release"], False)
    assert leave["notRestored"] == _state(["request:screen", "release"], False)
    assert leave["back"] == _state(["request:screen", "release", "request:screen"], True)


@needs_node
@pytest.mark.parametrize("case", ["offWhilePending", "leftWhilePending"])
def test_turned_off_or_left_while_pending_the_late_lock_is_let_go(case: str) -> None:
    assert _results()[case] == _state(["request:screen", "release"], False)


@needs_node
@pytest.mark.parametrize("refusal", ["rejects", "throws"])
def test_a_refusal_is_quiet_and_asked_again_later(refusal: str) -> None:
    assert _results()[refusal] == _state(["request:screen", "request:screen"], False)


@needs_node
def test_without_the_api_nothing_breaks_and_the_hint_says_so() -> None:
    results = _results()

    assert results["noApi"] == {
        "supported": False,
        "hints": [False, False],
        "log": [],
        "held": False,
    }
    assert results["settingsPage"] == {"supported": True, "hints": [True, True], "log": []}


def test_the_setting_is_off_by_default_and_survives_a_reset() -> None:
    script = (_ROOT / "app/static/js/reader-settings.js").read_text(encoding="utf-8")

    assert "keepScreenOn: false," in script
    kept = script.split("const KEPT_ON_RESET = [")[1].split("]")[0]
    assert '"keepScreenOn"' in kept


@pytest.mark.parametrize("template", ["chapter.html", "settings_reading.html"])
def test_both_places_have_the_switch_and_the_script(template: str) -> None:
    html = (_TEMPLATES / template).read_text(encoding="utf-8")

    assert 'data-setting="keepScreenOn"' in html
    # After reader-settings.js, whose settings it reads.
    assert html.index("js/reader-settings.js") < html.index("js/reader-wake-lock.js")


def test_the_settings_page_says_when_the_browser_cant() -> None:
    html = (_TEMPLATES / "settings_reading.html").read_text(encoding="utf-8")

    assert 'data-role="keep-screen-on-unsupported" hidden' in html

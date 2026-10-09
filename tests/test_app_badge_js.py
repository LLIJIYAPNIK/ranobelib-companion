"""PR 337: the unread count on the app icon (Badging API, app/static/js/app-badge.js).

The module runs under Node with a fake navigator and page - see
tests/js/app_badge_harness.mjs; those tests are skipped where Node isn't installed. Where
it's loaded, and that notifications-panel.js feeds it, is checked on the pages.
"""

import json
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app

_ROOT = Path(__file__).resolve().parent.parent
_HARNESS = _ROOT / "tests" / "js" / "app_badge_harness.mjs"
_SCRIPT = _ROOT / "app" / "static" / "js" / "app-badge.js"
_BASE = _ROOT / "app" / "templates" / "base.html"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")

client = TestClient(app)


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


@needs_node
def test_the_badge_follows_the_bells_count() -> None:
    with_bell = _results()["withBell"]

    assert with_bell["supported"] is True
    assert with_bell["atLoad"] == []  # the bell's first count sets it
    # 3, 3 again (no call), 12, "7", 0 (cleared), 0 and -2 (already clear), 5.6 -> 5.
    assert with_bell["calls"] == [["set", 3], ["set", 12], ["set", 7], ["clear"], ["set", 5]]


@needs_node
def test_a_page_without_the_bell_clears_a_leftover_badge() -> None:
    results = _results()

    assert results["noBell"] == [["clear"]]
    assert results["noBellStillLoading"] == {"before": [], "after": [["clear"]]}


@needs_node
def test_clear_takes_the_count_off_once() -> None:
    assert _results()["clear"] == [["set", 4], ["clear"]]


@needs_node
def test_without_the_api_nothing_is_called_and_nothing_breaks() -> None:
    results = _results()

    assert results["noApiErrors"] == []
    for case in ("noApiBell", "noApiNoBell"):
        assert results[case] == {"supported": False, "calls": []}, case


@needs_node
@pytest.mark.parametrize("refusal", ["rejects", "throws"])
def test_a_refusing_api_is_swallowed(refusal: str) -> None:
    result = _results()[refusal]

    assert result["errors"] == []
    assert result["calls"] == [["clear"], ["set", 2], ["clear"]]


@needs_node
def test_loaded_twice_is_one_badge() -> None:
    assert _results()["loadedTwice"] is True


def test_every_page_loads_it_guests_too() -> None:
    page = client.get("/").text
    head = page.split("</head>")[0]

    assert "static/js/app-badge.js" in head


def test_it_loads_before_the_scripts_that_set_and_clear_it() -> None:
    base = _BASE.read_text(encoding="utf-8")
    badge = base.index("js/app-badge.js")

    assert badge < base.index("js/device-account.js")
    assert badge < base.index("js/notifications-panel.js")
    assert base.index("js/app-badge.js") < base.index("{% if current_user %}")


def test_the_bell_and_logout_feed_it() -> None:
    panel = (_ROOT / "app/static/js/notifications-panel.js").read_text(encoding="utf-8")
    device = (_ROOT / "app/static/js/device-account.js").read_text(encoding="utf-8")

    # Optional calls: a page where app-badge.js didn't load still works.
    apply = panel.split("function applyUnreadCount(count) {")[1].split("\n  }")[0]
    assert "window.appBadge?.update(count);" in apply
    clear_personal = device.split("async function clearPersonal() {")[1].split("\n  }")[0]
    assert "window.appBadge?.clear();" in clear_personal

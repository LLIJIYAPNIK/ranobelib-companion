"""Keyboard for the «Библиотека / Каталог» switch - app/static/js/library-switch.js
(PR 294), run under Node with a fake tablist (tests/js/library_switch_harness.mjs).
Skipped where Node isn't installed.
"""

import json
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "app" / "static" / "js" / "library-switch.js"
_HARNESS = _ROOT / "tests" / "js" / "library_switch_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


@cache
def _scenarios() -> dict[str, dict[str, Any]]:
    completed = subprocess.run(
        ["node", str(_HARNESS), str(_SCRIPT)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(completed.stdout)


def test_arrow_right_moves_to_the_catalog_and_follows_it() -> None:
    result = _scenarios()["rightFromLibrary"]
    assert result["focused"] == 1
    assert result["tabStops"] == [-1, 0]
    assert result["navigations"] == ["/catalog"]
    assert result["prevented"] == ["ArrowRight"]


def test_arrow_left_wraps_around() -> None:
    assert _scenarios()["leftFromLibraryWraps"]["navigations"] == ["/catalog"]


def test_arrow_left_from_the_catalog_goes_back_to_the_library() -> None:
    result = _scenarios()["leftFromCatalog"]
    assert result["focused"] == 0
    assert result["navigations"] == ["/library"]


def test_home_and_end() -> None:
    assert _scenarios()["endFromLibrary"]["navigations"] == ["/catalog"]
    # Already on the first (active) item - nothing to navigate to.
    assert _scenarios()["homeOnLibrary"]["navigations"] == []


def test_space_on_the_active_item_stays_put() -> None:
    result = _scenarios()["spaceOnActive"]
    assert result["navigations"] == []
    assert result["prevented"] == [" "]


def test_other_keys_and_focus_elsewhere_are_left_alone() -> None:
    for scenario in ("otherKeysIgnored", "focusOutsideIgnored"):
        result = _scenarios()[scenario]
        assert result["navigations"] == []
        assert result["prevented"] == []
        assert result["tabStops"] == [0, -1]

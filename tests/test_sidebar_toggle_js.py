"""PR 310 sidebar state persistence, executed under Node across fake navigations."""

import json
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "app/static/js/sidebar-expand-init.js"
TOGGLE = ROOT / "app/static/js/sidebar-toggle.js"
HARNESS = ROOT / "tests/js/sidebar_toggle_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


@cache
def _result() -> dict[str, Any]:
    completed = subprocess.run(
        ["node", str(HARNESS), str(INIT), str(TOGGLE)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(completed.stdout)


def test_expanded_state_is_restored_synchronously_after_navigation() -> None:
    result = _result()

    assert result["afterExpand"]["stored"] == "1"
    assert result["secondBeforeDeferred"] == ["sidebar--expanded", "sidebar--initializing"]
    assert result["secondAfterDeferred"]["ariaExpanded"] == "true"
    assert result["secondAfterDeferred"]["label"] == "Свернуть меню"
    assert result["secondAfterPaint"] == ["sidebar--expanded"]


def test_collapsed_state_is_restored_on_the_next_navigation() -> None:
    result = _result()

    assert result["afterCollapse"]["stored"] == "0"
    assert result["thirdBeforeDeferred"] == ["sidebar--initializing"]
    assert result["thirdAfterDeferred"]["ariaExpanded"] == "false"
    assert result["thirdAfterDeferred"]["label"] == "Развернуть меню"


def test_toggle_announces_each_geometry_change() -> None:
    result = _result()

    assert result["afterExpand"]["events"] == [
        {"type": "sidebar:statechange", "detail": {"expanded": True}}
    ]
    assert result["afterCollapse"]["events"] == [
        {"type": "sidebar:statechange", "detail": {"expanded": False}}
    ]


def test_collapsed_rail_labels_become_tooltips_and_expanded_drops_them() -> None:
    result = _result()

    collapsed = ["Главная", "Войти", None]
    assert result["firstAfterDeferred"]["titles"] == collapsed
    assert result["afterExpand"]["titles"] == [None, None, None]
    assert result["secondAfterDeferred"]["titles"] == [None, None, None]
    assert result["afterCollapse"]["titles"] == collapsed
    assert result["thirdAfterDeferred"]["titles"] == collapsed

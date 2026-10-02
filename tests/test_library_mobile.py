"""PR 282 (Webnovells Mobile, wave 34): the library screen on phones."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")


def _rule(selector: str, *, indent: str = "") -> str:
    """The selector's last rule at this indent - for an indented one, the <= 767px block."""
    matches = re.findall(rf"\n{indent}{re.escape(selector)} \{{([^}}]*)\}}", CSS)
    assert matches, selector
    return matches[-1]


def test_library_tabs_are_a_ribbon() -> None:
    tabs = (ROOT / "app/templates/_library_tabs.html").read_text(encoding="utf-8")
    page = (ROOT / "app/templates/library.html").read_text(encoding="utf-8")
    assert '<nav class="ui-tabs" aria-label="Библиотека" data-ribbon>' in tabs
    assert "js/ribbon.js" in page

    ribbon = _rule(".wn-library .ui-tabs", indent="  ")
    assert "overflow-x: auto;" in ribbon
    assert "position: relative;" in ribbon  # offsetLeft of the active tab is measured against it
    assert "min-height: 44px;" in _rule(".wn-library .ui-tabs__link", indent="  ")


def test_ribbon_fades_only_where_there_is_more_and_reveals_the_active_item() -> None:
    script = (ROOT / "app/static/js/ribbon.js").read_text(encoding="utf-8")
    assert '"wn-ribbon--more-start"' in script
    assert '"wn-ribbon--more-end"' in script
    assert '[aria-current="page"], [aria-selected="true"]' in script
    assert "mask-image" in _rule(".wn-ribbon--more-start")
    assert "mask-image" in _rule(".wn-ribbon--more-end")

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


def test_search_takes_its_own_row_and_the_add_form_wraps() -> None:
    search = _rule(".wn-library-search", indent="  ")
    assert "flex: 1 1 100%;" in search
    assert "flex-wrap: wrap;" in _rule(".wn-library__add", indent="  ")
    assert "flex: 1 1 200px;" in _rule(".wn-library__add-field", indent="  ")
    assert "min-width: 128px;" in _rule(".wn-library__add .wn-btn", indent="  ")


def test_filters_button_chips_and_sheet_are_in_the_toolbar() -> None:
    page = (ROOT / "app/templates/library.html").read_text(encoding="utf-8")
    assert 'data-role="library-filters-open" aria-haspopup="dialog"' in page
    assert 'data-role="library-filters-count" hidden' in page
    assert 'data-role="library-filter-chips"' in page
    assert 'id="library-filters" data-bottom-sheet-title="Фильтры и сортировка" hidden' in page
    # One radio group per existing select - the selects stay the single source of state.
    assert 'data-filter-for="library-sort"' in page
    assert 'data-filter-for="library-progress"' in page


def test_sheet_radios_drive_the_selects() -> None:
    script = (ROOT / "app/static/js/library-toolbar.js").read_text(encoding="utf-8")
    assert "for (const option of select.options)" in script
    assert 'select.dispatchEvent(new Event("change"))' in script
    assert "select.selectedIndex !== 0" in script  # the count: controls off their default
    assert "`Показать ${visibleTotal}`" in script
    assert "window.bottomSheet.open(" in script


def test_mobile_toolbar_swaps_selects_for_the_filters_button() -> None:
    assert "display: none;" in _rule(".wn-library-select", indent="  ")
    assert "display: flex;" in _rule(".wn-library-filters-btn", indent="  ")
    view_button = _rule(".wn-library-view button", indent="  ")
    assert "width: 44px;" in view_button
    assert "height: 44px;" in view_button
    # Desktop keeps the selects; the mobile controls stay out of its layout.
    assert "display: none;" in _rule(".wn-library-filters-btn,\n.wn-library-chips")

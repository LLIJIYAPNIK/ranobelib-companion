"""Milestone 1 (#411, sub-issues #402–#410): UI polish regressions.

Verifies the CSS tokens, stacking contexts, focus-visible behavior, popover glass
styling, volume spine clipping/capping, and overflow guards across issues #402–#410.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
DROPDOWN_JS = (ROOT / "app/static/js/custom-dropdown.js").read_text(encoding="utf-8")
TITLE_UI_JS = (ROOT / "app/static/js/title-page-ui.js").read_text(encoding="utf-8")
TITLE_FRAGMENT = (ROOT / "app/templates/_title_content.html").read_text(encoding="utf-8")


def _rule(selector: str, *, last: bool = False) -> str:
    matches = re.findall(rf"\n(?:  )?{re.escape(selector)} \{{([^}}]*)\}}", CSS)
    assert matches, selector
    return matches[-1] if last else matches[0]


def test_402_hm_link_has_no_underline_and_keeps_focus_ring() -> None:
    link = _rule(".hm-link")
    assert "text-decoration: none;" in link
    assert "text-decoration: underline" not in link
    assert "box-shadow: var(--wn-focus-ring);" in _rule(".hm-link:focus-visible")
    assert "color: #fff;" in _rule(".hm-link:hover")


def test_403_library_sort_toolbar_stacks_above_shelves_and_below_search_suggest() -> None:
    tools = _rule(".lb-tools")
    assert "position: relative;" in tools
    assert "z-index: 20;" in tools
    open_tools = _rule('.lb-tools:has(.lb-sort__btn[aria-expanded="true"])')
    assert "z-index: 50;" in open_tools
    suggest_host = _rule(".hm-rise:has(.hm-search.is-suggesting)")
    assert "z-index: 60;" in suggest_host

    sort_list = _rule(".lb-sort__list")
    assert "background: var(--wn-popover-bg);" in sort_list
    assert "backdrop-filter: var(--wn-popover-blur);" in sort_list
    assert "border: 1px solid var(--wn-popover-border);" in sort_list

    catalog_toolbar = _rule(".hm--catalog.wn-catalog .catalog-toolbar")
    assert "position: relative;" in catalog_toolbar
    assert "z-index: 20;" in catalog_toolbar


def test_404_format_dropdown_uses_popover_tokens_and_focus_visible_only() -> None:
    assert ".ui-select:focus-within {" not in CSS
    focus_rule = _rule(".ui-select:has(> select:focus-visible, .dropdown__trigger:focus-visible)")
    assert "box-shadow: var(--wn-focus-ring);" in focus_rule

    listbox = _rule(".ui-select .dropdown__listbox")
    assert "background: var(--wn-popover-bg);" in listbox
    assert "backdrop-filter: var(--wn-popover-blur);" in listbox
    assert "border: 1px solid var(--wn-popover-border);" in listbox
    assert "box-shadow: var(--wn-popover-shadow);" in listbox

    # Mouse clicks do not force focus back onto the trigger button.
    assert "close(event.detail === 0)" in DROPDOWN_JS


def test_405_volume_spines_cap_width_for_small_volume_counts() -> None:
    assert "max-width: 148px;" in _rule(".tp-spine")
    assert "max-width: 120px;" in _rule(".tp-spine", last=True)
    assert "const max = phone() ? 120 : 148;" in TITLE_UI_JS
    assert "{% if contents | length > 1 %}" in TITLE_FRAGMENT


def test_406_volume_spine_fill_is_clipped_to_spine_radius() -> None:
    clip = _rule(".tp-spine__clip")
    assert "overflow: hidden;" in clip
    assert "border-radius: inherit;" in clip
    assert "overflow: hidden" not in _rule(".tp-spine")
    assert (
        '<span class="tp-spine__clip" aria-hidden="true">'
        '<span class="tp-spine__fill"></span></span>' in TITLE_FRAGMENT
    )


def test_407_translator_overflow_is_expandable_details() -> None:
    assert "Главы переводили разные команды:" in TITLE_FRAGMENT
    assert '<details class="tp-teams__more">' in TITLE_FRAGMENT
    assert "box-shadow: var(--wn-focus-ring);" in _rule(".tp-teams__toggle:focus-visible")
    assert "display: none;" in _rule(".tp-teams__more[open] > summary")


def test_408_search_suggest_uses_glass_popover_text_mark_and_thin_scrollbar() -> None:
    suggest = _rule(".hm-suggest")
    assert "background: var(--wn-popover-bg);" in suggest
    assert "backdrop-filter: var(--wn-popover-blur);" in suggest
    assert "border: 1px solid var(--wn-popover-border);" in suggest
    assert "scrollbar-width: thin;" in suggest
    assert "scrollbar-color:" in suggest

    mark = _rule(".hm-suggest mark")
    assert "background: transparent;" in mark
    assert "color: var(--wn-teal);" in mark

    # .hm-search renders glass on ::before so it does not create a backdrop root over .hm-suggest.
    assert "backdrop-filter" not in _rule(".hm-search")
    assert "backdrop-filter: var(--hm-glass-blur);" in _rule(".hm-search::before")
    assert ".hm-search:focus-within {" not in CSS


def test_409_shell_clips_horizontal_ambient_overflow_without_breaking_sticky() -> None:
    shell = _rule(".app-shell")
    assert "overflow-x: clip;" in shell
    assert "overflow-x: hidden" not in shell
    assert "min-width: 0;" in _rule(".tp-content")
    assert "min-width: 0;" in _rule(".tp-about")
    assert "overflow-wrap: anywhere;" in _rule(".tp-summary")
    assert "visibility: hidden;" in _rule(".dropdown__listbox")


def test_410_library_actions_menu_uses_shared_glass_popover_tokens() -> None:
    popover = _rule(".wn-library-menu,\n.wn-library-confirm")
    assert "background: var(--wn-popover-bg);" in popover
    assert "backdrop-filter: var(--wn-popover-blur);" in popover
    assert "border: 1px solid var(--wn-popover-border);" in popover
    assert "border-radius: var(--wn-popover-radius);" in popover
    assert "box-shadow: var(--wn-popover-shadow);" in popover

    item_focus = _rule(".wn-library-menu__item:focus-visible")
    assert "box-shadow: inset 0 0 0 2px var(--wn-teal);" in item_focus

    sheet_reset = _rule(".bottom-sheet :is(.wn-library-menu, .wn-library-confirm)")
    assert "backdrop-filter: none;" in sheet_reset
    assert "animation: none;" in sheet_reset

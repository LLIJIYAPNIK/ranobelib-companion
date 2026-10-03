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


def test_library_switch_spans_the_row_on_phones() -> None:
    # PR 294: the «Библиотека / Каталог» switch replaced PR 282's tab ribbon - both items
    # fit even at 320px, so there's nothing to scroll and no ribbon.js on the page.
    page = (ROOT / "app/templates/library.html").read_text(encoding="utf-8")
    assert '{% include "_library_switch.html" %}' in page
    assert "js/ribbon.js" not in page
    assert "width: 100%;" in _rule(".wn-lib-switch", indent="  ")
    assert "padding: 0 10px;" in _rule(".wn-lib-switch__tab", indent="  ")


def test_ribbon_fades_only_where_there_is_more_and_reveals_the_active_item() -> None:
    script = (ROOT / "app/static/js/ribbon.js").read_text(encoding="utf-8")
    assert '"wn-ribbon--more-start"' in script
    assert '"wn-ribbon--more-end"' in script
    assert '[aria-current="page"], [aria-selected="true"]' in script
    assert "mask-image" in _rule(".wn-ribbon--more-start")
    assert "mask-image" in _rule(".wn-ribbon--more-end")


def _phone_rule(selector: str, *, last: bool = True) -> str:
    """PR 300: the last rule for `selector` in the LibraryMobile section (767px, then
    359px blocks) - the first with last=False."""
    block = CSS.split("LibraryMobile (PR 300", 1)[1].split("title page (PR 253)", 1)[0]
    matches = re.findall(rf"\n  {re.escape(selector)} \{{([^}}]*)\}}", block)
    assert matches, selector
    return matches[-1] if last else matches[0]


def test_search_takes_its_own_row_and_the_add_form_wraps() -> None:
    search = _rule(".wn-library-search", indent="  ")
    assert "flex: 1 1 100%;" in search
    assert "flex-wrap: wrap;" in _rule(".wn-library__add", indent="  ")
    # PR 300 (LibraryMobile): a 180px+ field beside a 112px+ «Добавить» - the button
    # drops under the field at 320px.
    assert "flex: 1 1 180px;" in _phone_rule(".wn-library .wn-library__add-field")
    button = _phone_rule(".wn-library .wn-library__add .wn-btn")
    assert "min-width: 112px;" in button
    assert "background: rgb(255 255 255 / 6%);" in button  # glass, not the purple CTA


def test_head_is_eyebrow_h1_then_the_switch() -> None:
    assert "800 30px/1.05" in _phone_rule(".wn-library .wn-library__title")
    head = _phone_rule(".wn-library .wn-library__head")
    assert "flex-direction: column;" in head
    assert "gap: 14px;" in head


def test_add_field_gets_a_short_placeholder_on_phones() -> None:
    page = (ROOT / "app/templates/library.html").read_text(encoding="utf-8")
    assert 'data-placeholder-short="Ссылка на тайтл"' in page
    assert "js/library-mobile.js" in page
    script = (ROOT / "app/static/js/library-mobile.js").read_text(encoding="utf-8")
    assert "phone.matches ? field.dataset.placeholderShort : full" in script


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


def test_grid_view_fits_two_columns_on_phones() -> None:
    grid = _rule(
        ".wn-library__titles--grid .wn-library-grid,\n"
        "  .wn-library__titles--grid .wn-library-posters",
        indent="  ",
    )
    assert "repeat(auto-fill, minmax(128px, 1fr))" in grid


def test_narrow_grid_cards_keep_their_title_and_cta_inside() -> None:
    name = _rule(".wn-library__titles--grid .wn-library-card__name", indent="  ")
    assert "-webkit-line-clamp: unset;" in name  # titles wrap in full, no ellipsis
    assert "display: none;" in _rule(
        ".wn-library__titles--grid .wn-library-card__cta-verb", indent="  "
    )


def test_continue_button_is_a_44px_target_on_phones() -> None:
    # Found by the 320/375/430 check: «Продолжить» was 38px tall in list and grid alike.
    assert "min-height: 44px;" in _rule(".wn-library-card__cta", indent="  ")


def test_hero_is_compact_with_the_cover_beside_the_copy() -> None:
    inner = _phone_rule(".wn-library .wn-library-hero__inner", last=False)
    assert "grid-template-columns: 88px minmax(0, 1fr);" in inner
    assert "grid-row: 1 / 5;" in _phone_rule(".wn-library .wn-library-hero__cover")
    narrow = CSS.split("@media (max-width: 359px) {\n  .wn-library .wn-library-hero__inner {", 1)[1]
    assert "grid-template-columns: 76px minmax(0, 1fr);" in narrow.split("}", 1)[0]
    actions = _phone_rule(".wn-library .wn-library-hero__actions")
    assert "grid-column: 1 / -1;" in actions
    assert "flex: 1;" in _phone_rule(".wn-library .wn-library-hero__continue")


def test_hero_toc_moves_into_its_action_sheet_on_phones() -> None:
    page = (ROOT / "app/templates/library.html").read_text(encoding="utf-8")
    assert 'actions_menu(hero_entry.slug_url, hero_name, toc="phone")' in page
    assert '{% if toc == "phone" %} wn-library-menu__item--phone-only{% endif %}' in page
    assert re.search(
        r"@media \(min-width: 768px\) \{\n  \.wn-library-menu__item--phone-only \{\n"
        r"    display: none;",
        CSS,
    )

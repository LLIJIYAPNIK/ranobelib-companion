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


def test_sort_and_progress_buttons_and_the_sheet_are_in_the_toolbar() -> None:
    page = (ROOT / "app/templates/library.html").read_text(encoding="utf-8")
    # PR 300 (LibraryMobile): two buttons, one sheet - «Фильтры» and its value chips went.
    assert page.count('data-role="library-filters-open" aria-haspopup="dialog"') == 2
    assert '<span data-role="library-sort-label">Недавно читал</span>' in page
    assert 'data-role="library-filters-count" hidden>1</span>' in page
    assert 'data-role="library-filter-chips"' not in page
    assert 'id="library-filters" data-bottom-sheet-title="Сортировка и прогресс" hidden' in page
    assert "data-bottom-sheet-close>Показать</button>" in page
    assert 'data-role="library-filters-reset"' not in page
    # One option group per existing select - the selects stay the single source of state.
    assert 'data-filter-for="library-sort"' in page
    assert 'data-filter-for="library-progress"' in page


def test_sheet_is_a_draft_until_show() -> None:
    script = (ROOT / "app/static/js/library-toolbar.js").read_text(encoding="utf-8")
    assert "for (const option of select.options)" in script
    # Picking an option no longer touches the select; «Показать» does, and closing the
    # sheet any other way re-checks the options from the selects.
    assert 'input.addEventListener("change"' not in script
    assert 'filtersDone?.addEventListener("click", () => {' in script
    assert "onClose: syncRadios," in script
    assert "window.bottomSheet.open(" in script


def test_toolbar_buttons_show_the_sort_and_a_progress_count() -> None:
    script = (ROOT / "app/static/js/library-toolbar.js").read_text(encoding="utf-8")
    assert "if (sortLabel) sortLabel.textContent = sortText;" in script
    assert "if (filtersCount) filtersCount.hidden = !progressSet;" in script
    assert 'sortOpen?.toggleAttribute("data-changed", sort.selectedIndex !== 0);' in script
    assert "display: none;" in _rule(".wn-library-select", indent="  ")
    assert "flex: 1 1 0;" in _phone_rule(".wn-library-sheet-btn--sort")
    assert "border-color: rgb(61 214 195 / 40%);" in _phone_rule(
        ".wn-library-sheet-btn[data-changed]"
    )
    # Desktop keeps the selects; the phone controls stay out of its layout.
    assert "display: none;" in _rule(".wn-library-sheet-btn,\n.wn-library-search__clear")


def test_search_clear_and_results_chips_on_phones() -> None:
    page = (ROOT / "app/templates/library.html").read_text(encoding="utf-8")
    assert 'data-role="library-search-clear" aria-label="Очистить поиск" hidden' in page
    assert 'Сбросить<span class="wn-library-criteria__reset-all"> всё</span>' in page
    strip = _phone_rule(".wn-library .wn-library-criteria:not([hidden])")
    assert "overflow-x: auto;" in strip
    assert "display: none;" in _phone_rule(
        ".wn-library .wn-library-search input::-webkit-search-cancel-button"
    )


def test_sheet_options_are_pills_with_a_pinned_show_button() -> None:
    option = re.search(r"\n\.wn-library-filters__option \{([^}]*)\}", CSS)
    assert option
    assert "border-radius: 999px;" in option[1]
    actions = re.search(r"\n\.wn-library-filters__actions \{([^}]*)\}", CSS)
    assert actions
    assert "position: sticky;" in actions[1]


def test_phones_list_row_cards_only() -> None:
    # PR 300 (LibraryMobile): one column of LibraryCard row - the grid view and its
    # toggle are desktop-only now.
    assert "display: none;" in _phone_rule(".wn-library .wn-library-view")
    assert "grid-template-columns: minmax(0, 1fr);" in _phone_rule(".wn-library .wn-library-grid")
    assert "wn-library__titles--grid .wn-library-card__cta-verb" not in CSS


def test_row_card_puts_the_menu_beside_continue() -> None:
    body = _phone_rule(".wn-library .wn-library-card__body")
    assert "grid-template-columns: minmax(0, 1fr) 44px;" in body
    assert "display: contents;" in _phone_rule(".wn-library .wn-library-card__top")
    cta = _phone_rule(".wn-library .wn-library-card__cta")
    assert "grid-column: 1;" in cta
    assert "background: rgb(61 214 195 / 7%);" in cta
    more = _phone_rule(".wn-library .wn-library-card .wn-library-more")
    assert "grid-column: 2;" in more
    assert "width: 44px;" in more
    assert "width: 76px;" in _phone_rule(".wn-library .wn-library-card__cover")


def test_row_card_drops_the_verb_under_360px() -> None:
    assert re.search(
        r"@media \(max-width: 359px\) \{\n  \.wn-library \.wn-library-card__cta-verb \{\n"
        r"    display: none;",
        CSS,
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


def test_card_menu_opens_as_a_bottom_sheet_on_phones() -> None:
    script = (ROOT / "app/static/js/library-card-actions.js").read_text(encoding="utf-8")
    assert 'const phone = window.matchMedia("(max-width: 767px)");' in script
    assert "if (phone.matches && window.bottomSheet) {" in script
    assert 'title: confirming ? "Удалить из библиотеки?" : button.dataset.titleName,' in script
    page = (ROOT / "app/templates/library.html").read_text(encoding="utf-8")
    assert 'data-title-name="{{ name }}"' in page
    assert 'data-role="library-confirm-cancel" data-autofocus>Отмена</button>' in page


def test_confirm_sheet_has_a_pink_top_edge_and_48px_buttons() -> None:
    edge = re.search(
        r"\n\.bottom-sheet__panel:has\(\.wn-library-confirm:not\(\[hidden\]\)\) \{([^}]*)\}", CSS
    )
    assert edge
    assert "border-top-color: rgb(240 138 155 / 40%);" in edge[1]
    buttons = re.search(
        r"\n\.bottom-sheet :is\(\.wn-library-confirm__cancel, \.wn-library-confirm__remove\) "
        r"\{([^}]*)\}",
        CSS,
    )
    assert buttons
    assert "min-height: 48px;" in buttons[1]

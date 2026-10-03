"""PR 299 (wave 36): the catalog screen on phones (CatalogMobile.dc.html / Catalog handoff.md)."""

import re
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from ranobelib import CatalogPage
from ranobelib.models import Genre

from app.main import app
from tests.test_api_catalog import _FakeCatalog, _titles

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
client = TestClient(app)


def _mobile_rule(selector: str, *, last: bool = True) -> str:
    """The body of the last rule for `selector` inside the CatalogMobile section's
    max-width blocks (767px, then 359px) - the first with last=False."""
    block = CSS.split("CatalogMobile (PR 299", 1)[1].split("LibraryDesktop (PR 297", 1)[0]
    matches = [
        body
        for selectors, body in re.findall(r"\n  ([^{}@/]+?) \{([^}]*)\}", block)
        if selector in [part.strip() for part in selectors.split(",")]
    ]
    assert matches, selector
    return matches[-1] if last else matches[0]


def _get(**params: object) -> str:
    page = CatalogPage(items=_titles(1, 3), page=1, has_next_page=False)
    genres = [Genre(id=5, name="Фэнтези")]
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page, genres=genres)):
        return client.get("/catalog", params=params).text


def test_h1_and_the_app_bar_say_library_on_phones() -> None:
    html = _get()
    assert '<h1 class="wn-catalog__title">Библиотека</h1>' in html
    assert '<span class="sidebar__strip-title">Библиотека</span>' in html
    assert "800 28px/1.15" in _mobile_rule(".wn-catalog__title")
    assert "display: none;" in _mobile_rule(".wn-catalog__eyebrow")


def test_search_has_its_own_row_and_a_clear_button() -> None:
    html = _get(query="dxd")
    assert 'enterkeyhint="search"' in html
    assert 'data-role="catalog-search-clear"' in html
    assert 'aria-label="Очистить поиск" hidden' not in html  # a search to clear
    assert 'aria-label="Очистить поиск" hidden' in _get()  # nothing to clear yet
    search = _mobile_rule(".wn-catalog .catalog-toolbar__search")
    assert "flex: 1 1 100%;" in search
    assert "height: 48px;" in search


def test_sort_filters_and_view_share_one_row() -> None:
    assert "flex: 1 1 0;" in _mobile_rule(".wn-catalog .catalog-toolbar__sort")
    assert "padding: 0 12px;" in _mobile_rule(".wn-catalog .catalog-toolbar__filters")
    # One look for all three: 44px, #16161f, a 12px radius.
    assert re.search(
        r"\.wn-catalog \.catalog-toolbar__sort,\n  \.wn-catalog \.catalog-toolbar__filters,\n"
        r"  \.catalog-toolbar__view:not\(\[hidden\]\) \{\n    height: 44px;",
        CSS,
    )
    view = _mobile_rule(".catalog-toolbar__view:not([hidden])")
    assert "width: 44px;" in view
    html = _get()
    assert (
        'data-role="catalog-view-toggle"\n      aria-label="Показать списком"\n      hidden' in html
    )
    assert "js/catalog-mobile.js" in html


def test_view_toggle_is_remembered() -> None:
    script = (ROOT / "app/static/js/catalog-mobile.js").read_text(encoding="utf-8")
    assert 'const VIEW_KEY = "catalogView";' in script
    assert "grid.dataset.view = view;" in script
    assert 'saved === "list" ? "list" : "grid"' in script


def test_criteria_chips_are_one_strip_with_a_short_reset() -> None:
    html = _get(query="dxd")
    assert 'Сбросить<span class="catalog-criteria__reset-all"> всё</span></a>' in html
    strip = _mobile_rule(".wn-catalog .catalog-criteria")
    assert "flex-wrap: nowrap;" in strip
    assert "overflow-x: auto;" in strip
    assert "display: none;" in _mobile_rule(".catalog-criteria__reset-all")


def test_grid_is_strictly_two_columns() -> None:
    grid = _mobile_rule(".wn-catalog .catalog-grid")
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in grid
    assert "gap: 24px 12px;" in grid
    assert "display: none;" in _mobile_rule(".catalog-section")


def test_card_lines_switch_to_genre_then_status_and_chapters() -> None:
    hidden = _mobile_rule(".catalog-card__meta-status")
    assert "display: none;" in hidden
    assert "display: inline;" in _mobile_rule(".catalog-card__sub-status")
    # Desktop keeps «жанр · статус» / «N гл.» - the phone-only parts are off there.
    assert re.search(
        r"\n\.catalog-card__sub-status,\n\.catalog-card__sub--status-only \{\n  display: none;",
        CSS,
    )


def test_list_view_is_a_row_card() -> None:
    assert "grid-template-columns: minmax(0, 1fr);" in _mobile_rule(
        '.wn-catalog .catalog-grid[data-view="list"]'
    )
    assert "width: 64px;" in _mobile_rule('.catalog-grid[data-view="list"] .catalog-card__media')
    action = _mobile_rule('.catalog-grid[data-view="list"] .catalog-card__action')
    assert "position: static;" in action


def test_featured_card_is_a_horizontal_block_with_the_cover_beside_the_copy() -> None:
    inner = _mobile_rule(".wn-catalog .catalog-featured__inner", last=False)
    assert "grid-template-columns: 92px minmax(0, 1fr);" in inner
    assert "display: contents;" in _mobile_rule(".wn-catalog .catalog-featured__copy")
    assert "grid-row: 1 / 5;" in _mobile_rule(".wn-catalog .catalog-featured__cover")
    narrow = CSS.split("@media (max-width: 359px) {\n  .wn-catalog .catalog-featured__inner {", 1)
    assert "grid-template-columns: 80px minmax(0, 1fr);" in narrow[1].split("}", 1)[0]
    backdrop = _mobile_rule(".wn-catalog .catalog-featured__backdrop")
    assert "filter: blur(40px) saturate(0.6) brightness(0.6);" in backdrop


def test_featured_card_shows_two_genres_and_an_icon_library_button() -> None:
    assert "display: none;" in _mobile_rule(
        ".wn-catalog .catalog-featured__genres li:nth-child(n + 3)"
    )
    assert "width: 44px;" in _mobile_rule(".wn-catalog .catalog-featured__lib")
    assert "display: none;" in _mobile_rule(".wn-catalog .catalog-featured__lib > span")
    # The hidden label is still announced: the toggle carries its own aria-label.
    cards = (ROOT / "app/templates/_catalog_cards.html").read_text(encoding="utf-8")
    assert "aria-label=\"{{ ('«' ~ name ~ '» в библиотеке — убрать')" in cards


def _sheet(html: str) -> str:
    return html.split('id="catalog-filters-sheet"', 1)[1].split("</form>", 1)[0]


def test_filters_sheet_is_its_own_draft_form() -> None:
    page = CatalogPage(items=_titles(1, 3), page=1, has_next_page=False)
    fake = _FakeCatalog(page, genres=[Genre(id=5, name="Фэнтези"), Genre(id=6, name="Уся")])
    params = {"query": "dxd", "sort": "views", "genres": 6, "tags": 9, "tag_name": "Магия"}
    with patch("app.services.catalog.Catalog", return_value=fake):
        sheet = _sheet(client.get("/catalog", params=params).text)
    assert 'data-bottom-sheet-title="Фильтры" hidden' in sheet
    assert 'method="get" action="/catalog"' in sheet
    assert 'data-default-sort="last_chapter_at" autocomplete="off"' in sheet
    # What the sheet doesn't show rides along as it is.
    assert '<input type="hidden" name="query" value="dxd"' in sheet
    assert '<input type="hidden" name="tags" value="9">' in sheet
    assert '<input type="hidden" name="tag_name" value="Магия">' in sheet
    # Sort: one; genres: several - the page's own state is the draft's start.
    assert '<input type="radio" name="sort" value="views" checked>' in sheet
    assert '<input type="radio" name="sort" value="last_chapter_at">' in sheet
    assert '<input type="checkbox" name="genres" value="6" checked>' in sheet
    assert '<input type="checkbox" name="genres" value="5">' in sheet
    assert ">Показать результаты</button>" in sheet
    assert "data-bottom-sheet-action>Сбросить</button>" in sheet


def test_sheet_buttons_show_the_sort_and_the_filter_count() -> None:
    html = _get(sort="views", genres=5)
    assert 'aria-label="Сортировка: По просмотрам"' in html
    assert "<span>По просмотрам</span>" in html
    assert 'class="catalog-toolbar__sheet-count" aria-label="выбрано: 1">1</span>' in html
    assert html.count('data-role="catalog-sheet-open"') == 2
    # They replace the select and the popover toggle only once the script is in.
    assert (
        "  .wn-catalog--sheet :is(.catalog-toolbar__sort, .catalog-toolbar__filters) {\n"
        "    display: none;"
    ) in CSS


def test_sheet_script_keeps_a_draft() -> None:
    script = (ROOT / "app/static/js/catalog-mobile.js").read_text(encoding="utf-8")
    assert "if (!applying) sheetForm.reset();" in script
    assert 'field.type === "radio" && field.value === sheetForm.dataset.defaultSort' in script
    assert "sheetQuery.value = input.value.trim();" in script
    assert 'page.classList.add("wn-catalog--sheet");' in script


def test_bottom_sheet_takes_a_head_action() -> None:
    script = (ROOT / "app/static/js/bottom-sheet.js").read_text(encoding="utf-8")
    assert 'content.querySelector("[data-bottom-sheet-action]")' in script
    assert "closeButton.before(actionEl);" in script
    assert "action?.home.replaceWith(action.el);" in script


def test_sheet_apply_stays_on_the_sheet_floor() -> None:
    foot = re.search(r"\n\.catalog-sheet__foot \{([^}]*)\}", CSS)
    assert foot
    assert "position: sticky;" in foot[1]
    assert "background: #16161f;" in foot[1]


def test_feed_states_follow_the_phone_mockup() -> None:
    error = _mobile_rule(".wn-catalog .catalog-feed-error")
    assert "justify-content: flex-start;" in error
    assert "flex: 1 1 140px;" in _mobile_rule(".wn-catalog .catalog-feed-error__copy")
    assert "display: none;" in _mobile_rule(".wn-catalog .catalog-feed-error__hint")
    assert "display: none;" in _mobile_rule(".wn-catalog .catalog-feed-end__diamond")
    assert "color: #b7a6ff;" in _mobile_rule(".wn-catalog .catalog-feed-end__action")
    empty = _mobile_rule(".wn-catalog .catalog-empty")
    assert "border: 1px dashed #33304a;" in empty
    assert "display: none;" in _mobile_rule(".wn-catalog .catalog-empty__icon")


def test_list_line_has_no_stray_space_before_the_separator() -> None:
    html = _get()
    assert '</span></span><span class="catalog-card__sub' in html

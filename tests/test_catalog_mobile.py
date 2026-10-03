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


def _mobile_rule(selector: str) -> str:
    """The body of the last rule for `selector` inside the CatalogMobile section's
    max-width: 767px blocks."""
    block = CSS.split("CatalogMobile (PR 299", 1)[1].split("LibraryDesktop (PR 297", 1)[0]
    matches = [
        body
        for selectors, body in re.findall(r"\n  ([^{}@/]+?) \{([^}]*)\}", block)
        if selector in [part.strip() for part in selectors.split(",")]
    ]
    assert matches, selector
    return matches[-1]


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

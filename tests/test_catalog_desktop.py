"""PR 296: the desktop catalog screen (CatalogDesktop.dc.html / Catalog handoff.md)."""

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


def _desktop_rule(selector: str, *, last: bool = True) -> str:
    """The selector's own rule inside the CatalogDesktop min-width: 768px block - the last
    one, so a shared `a, b {` rule earlier on doesn't shadow it."""
    block = CSS.split("CatalogDesktop (PR 296", 1)[1]
    matches = re.findall(rf"\n  {re.escape(selector)} \{{([^}}]*)\}}", block)
    assert matches, selector
    return matches[-1] if last else matches[0]


def _get(**params: object) -> str:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    genres = [Genre(id=5, name="Фэнтези")]
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page, genres=genres)):
        return client.get("/catalog", params=params).text


def test_header_card_has_the_eyebrow_title_and_the_switch() -> None:
    html = _get()
    hero = html.split('<section class="wn-catalog__hero">', 1)[1].split("</section>", 1)[0]
    assert '<span class="wn-catalog__eyebrow">Каталог RanobeLib</span>' in hero
    assert '<h1 class="wn-catalog__title">Библиотека</h1>' in hero
    assert 'data-role="library-switch"' in hero


def test_criteria_row_sits_inside_the_sticky_panel() -> None:
    html = _get(query="dxd")
    toolbar = html.split('data-role="catalog-scroll-header"', 1)[1].split("</header>", 1)[0]
    assert 'data-role="catalog-criteria"' in toolbar
    assert '<span class="catalog-criteria__label" aria-hidden="true">Условия</span>' in toolbar


def test_search_shows_the_slash_hint_and_the_shortcut_script_is_wired() -> None:
    html = _get()
    assert '<kbd class="wn-catalog__kbd" aria-hidden="true">/</kbd>' in html
    assert 'data-role="catalog-search-input"' in html
    assert "js/catalog-search-shortcut.js" in html
    script = (ROOT / "app/static/js/catalog-search-shortcut.js").read_text(encoding="utf-8")
    assert 'event.key !== "/"' in script
    assert '["INPUT", "TEXTAREA", "SELECT"]' in script


def test_filters_open_as_a_popover_under_their_button_on_desktop() -> None:
    script = (ROOT / "app/static/js/catalog-filters-toggle.js").read_text(encoding="utf-8")
    assert 'window.matchMedia("(min-width: 768px)")' in script
    assert "toggle.getBoundingClientRect()" in script
    popover = _desktop_rule(".wn-catalog .catalog-filters.catalog-filters--enhanced")
    assert "position: fixed;" in popover
    assert "width: 360px;" in popover


def test_desktop_shell_follows_the_design() -> None:
    title = _desktop_rule(".wn-catalog__title")
    assert "800 52px/1.02" in title
    results_title = _desktop_rule('.wn-catalog[data-mode="results"] .wn-catalog__title')
    assert "font-size: 36px;" in results_title
    toolbar = _desktop_rule(".wn-catalog .catalog-toolbar")
    assert "top: 12px;" in toolbar
    assert "margin: -44px auto 0;" in toolbar
    assert "backdrop-filter: blur(18px) saturate(1.2);" in toolbar
    assert "--wn-catalog-ctl-h: 52px;" in _desktop_rule(".wn-catalog")
    assert "--wn-catalog-ctl-h: 46px;" in _desktop_rule('.wn-catalog[data-mode="results"]')
    assert "box-shadow: inset 0 0 220px rgb(0 0 0 / 55%);" in _desktop_rule(".wn-catalog::after")


def test_search_button_goes_once_sort_autosubmits_at_every_width() -> None:
    assert re.search(
        r"\n\.catalog-toolbar--autosubmit \.catalog-toolbar__submit \{\s*display: none;", CSS
    )


# --- editorial feed: section labels -------------------------------------------------


def _labels(html: str) -> list[str]:
    return re.findall(r'<h2 class="catalog-section__title">([^<]+)</h2>', html)


def _feed(html: str) -> list[str]:
    """The grid's items in order: S(ection), F(eatured), C(ard)."""
    grid = html.split('data-role="catalog-grid"', 1)[-1]
    roles = re.findall(
        r'data-role="(catalog-section|catalog-featured|title-quickview-trigger)"', grid
    )
    return [{"catalog-section": "S", "catalog-featured": "F"}.get(r, "C") for r in roles]


def test_editorial_feed_opens_with_fresh_updates_and_continues_after_each_insert() -> None:
    fake = _FakeCatalog(
        CatalogPage(items=_titles(1, 30), page=1, has_next_page=True),
        featured=[CatalogPage(items=_titles(1001, 5), page=1, has_next_page=False)],
    )
    with patch("app.services.catalog.Catalog", return_value=fake):
        html = client.get("/catalog").text

    assert _labels(html) == ["Свежие обновления", "Продолжить каталог", "Продолжить каталог"]
    feed = _feed(html)
    assert feed[0] == "S"
    assert "".join(feed) == "S" + "C" * 12 + "FS" + "C" * 12 + "FS" + "C" * 6
    assert 'class="catalog-section catalog-section--fresh"' in html


def test_no_label_after_an_insert_that_ends_the_feed() -> None:
    fake = _FakeCatalog(
        CatalogPage(items=_titles(1, 12), page=1, has_next_page=False),
        featured=[CatalogPage(items=_titles(1001, 5), page=1, has_next_page=False)],
    )
    with patch("app.services.catalog.Catalog", return_value=fake):
        html = client.get("/catalog").text

    assert "".join(_feed(html)) == "S" + "C" * 12 + "F"


def test_later_pages_carry_no_opening_label() -> None:
    fake = _FakeCatalog(
        CatalogPage(items=_titles(31, 30), page=2, has_next_page=True),
        featured=[CatalogPage(items=_titles(1001, 10), page=1, has_next_page=False)],
    )
    with patch("app.services.catalog.Catalog", return_value=fake):
        html = client.get("/catalog/page", params={"page": 2, "shown": 30, "featured": 2}).text

    assert "".join(_feed(html)).startswith("CCCCCCF")
    assert _labels(html) == ["Продолжить каталог"] * 3


def test_results_mode_is_one_unnamed_grid() -> None:
    fake = _FakeCatalog(CatalogPage(items=_titles(1, 30), page=1, has_next_page=True))
    with patch("app.services.catalog.Catalog", return_value=fake):
        html = client.get("/catalog", params={"sort": "views"}).text

    assert _labels(html) == []


def test_desktop_grid_is_six_columns_with_the_mode_gaps() -> None:
    grid = _desktop_rule(".wn-catalog .catalog-grid", last=False)
    assert "gap: 40px 24px;" in grid
    results = _desktop_rule('.wn-catalog[data-mode="results"] .catalog-grid')
    assert "gap: 30px 20px;" in results
    wide = CSS.split("@media (min-width: 1200px) {\n  .wn-catalog .catalog-grid {", 1)[1]
    assert wide.lstrip().startswith("grid-template-columns: repeat(6, minmax(0, 1fr));")

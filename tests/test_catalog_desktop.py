"""PR 296: the desktop catalog screen (CatalogDesktop.dc.html / Catalog handoff.md)."""

import re
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from ranobelib import CatalogPage
from ranobelib.models import Genre

from app.main import app
from tests.test_api_catalog import _FakeCatalog

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
client = TestClient(app)


def _desktop_rule(selector: str) -> str:
    """The selector's own rule inside the CatalogDesktop min-width: 768px block - the last
    one, so a shared `a, b {` rule earlier on doesn't shadow it."""
    block = CSS.split("CatalogDesktop (PR 296", 1)[1]
    matches = re.findall(rf"\n  {re.escape(selector)} \{{([^}}]*)\}}", block)
    assert matches, selector
    return matches[-1]


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

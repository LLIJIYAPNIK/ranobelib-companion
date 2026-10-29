"""PR 296: the desktop catalog screen (CatalogDesktop.dc.html / Catalog handoff.md)."""

import re
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from ranobelib import CatalogPage
from ranobelib.models import Country, Cover, Genre, Label, Title

from app.main import app
from tests.test_api_catalog import _FakeCatalog, _titles

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
client = TestClient(app)


def _desktop_rule(selector: str, *, last: bool = True) -> str:
    """The body of a rule inside the CatalogDesktop min-width: 768px block whose selector
    list includes `selector` (PR 297: the library shares some of these rules) - the last
    such rule by default, the first with last=False."""
    block = CSS.split("CatalogDesktop (PR 296", 1)[1]
    # PR 299: the phone section in between isn't desktop.
    before_mobile, rest = block.split("CatalogMobile (PR 299", 1)
    block = before_mobile + rest.split("LibraryDesktop (PR 297", 1)[1]
    matches = [
        body
        for selectors, body in re.findall(r"\n  ([^{}@/]+?) \{([^}]*)\}", block)
        if selector in [part.strip() for part in selectors.split(",")]
    ]
    assert matches, selector
    return matches[-1] if last else matches[0]


def _get(**params: object) -> str:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    genres = [Genre(id=5, name="Фэнтези")]
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page, genres=genres)):
        return client.get("/catalog", params=params).text


def test_header_is_the_homes_with_the_switch() -> None:
    # «Разделы»: the home's frame and heading, as on the library's «Полки».
    html = _get()
    head = html.split('<header class="hm-head cg-head hm-rise">', 1)[1].split("</header>", 1)[0]
    assert '<h1 class="hm-title" id="catalog-title">Каталог</h1>' in head
    assert 'data-role="library-switch"' in head
    assert "wn-catalog__hero" not in html


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
    assert "top: calc(var(--shell-top) + 12px);" in toolbar
    assert "margin: -44px auto 0;" in toolbar
    assert "backdrop-filter: blur(18px) saturate(1.2);" in toolbar
    assert "--wn-catalog-ctl-h: 52px;" in _desktop_rule(".wn-catalog")
    assert "--wn-catalog-ctl-h: 46px;" in _desktop_rule('.wn-catalog[data-mode="results"]')
    assert "box-shadow: inset 0 0 220px rgb(0 0 0 / 55%);" in _desktop_rule(".wn-catalog::after")


def test_search_button_goes_once_sort_autosubmits_at_every_width() -> None:
    assert re.search(
        r"\n\.catalog-toolbar--autosubmit \.catalog-toolbar__submit \{\s*display: none;", CSS
    )


def test_results_mode_is_one_grid_with_no_sections() -> None:
    fake = _FakeCatalog(CatalogPage(items=_titles(1, 30), page=1, has_next_page=True))
    with patch("app.services.catalog.Catalog", return_value=fake):
        html = client.get("/catalog", params={"sort": "views"}).text

    assert 'class="cg-sections"' not in html
    assert html.count('data-role="catalog-card"') == 30


def test_desktop_grid_is_six_columns_with_the_mode_gaps() -> None:
    grid = _desktop_rule(".wn-catalog .catalog-grid", last=False)
    assert "gap: 40px 24px;" in grid
    results = _desktop_rule('.wn-catalog[data-mode="results"] .catalog-grid')
    assert "gap: 30px 20px;" in results
    wide = CSS.split("@media (min-width: 1200px) {\n  .wn-catalog .catalog-grid {", 1)[1]
    assert wide.lstrip().startswith("grid-template-columns: repeat(6, minmax(0, 1fr));")


# --- CatalogCard variant="cinema" ---------------------------------------------------


def _card_html(title: Title) -> str:
    fake = _FakeCatalog(CatalogPage(items=[title], page=1, has_next_page=False))
    with patch("app.services.catalog.Catalog", return_value=fake):
        html = client.get("/catalog", params={"sort": "views"}).text
    card = html.split('<div class="catalog-card" data-role="catalog-card"', 1)[1]
    return card.split('data-role="catalog-sentinel"', 1)[0]


def _title(**overrides: object) -> Title:
    fields: dict[str, object] = {
        "id": 7,
        "name": "Dragon",
        "rus_name": "Последнее пламя в долгой ночи",
        "slug": "dragon",
        "slug_url": "7--dragon",
        "cover": Cover(default="https://example.com/c.jpg"),
        "age_restriction": Label(id=0, label="16+"),
        "status": Label(id=1, label="Онгоинг"),
        "genres": [Genre(id=5, name="Тёмное фэнтези"), Genre(id=6, name="Выживание")],
    }
    fields.update(overrides)
    return Title(**fields)


def test_card_shows_title_status_and_country() -> None:
    card = _card_html(_title(country=Country(id=11, name="Корея")))

    assert 'data-slug-url="7--dragon"' in card
    assert 'href="/titles/7--dragon" title="Последнее пламя в долгой ночи"' in card
    assert '<span class="catalog-card__title">Последнее пламя в долгой ночи</span>' in card
    # The listing's genres are empty on the real catalog; its status and country are not.
    assert '<span class="catalog-card__meta">Онгоинг · Корея</span>' in card
    # PR 304/305: the listing never has a chapter count (SDK #74) - the card has no
    # «N гл.» line, even for a Title that happens to carry one.
    assert "гл." not in _card_html(_title(chapter_count=1133))
    assert 'class="catalog-card__img" src="https://example.com/c.jpg"' in card
    assert "catalog-card__glow" not in card
    assert 'href="/login" aria-label="Войдите, чтобы добавить' in card  # a guest


def test_card_without_a_country_or_cover_drops_them() -> None:
    card = _card_html(_title(cover=Cover()))

    assert '<span class="catalog-card__meta">Онгоинг</span>' in card
    assert "<img" not in card


def test_cinema_hover_follows_the_design() -> None:
    marker = "@media (min-width: 768px) and (hover: hover) {\n  .wn-catalog .catalog-card"
    hover = CSS.split(marker, 1)[1]
    assert ":hover {\n    transform: translateY(-4px);" in hover
    assert "transform: scale(1.03);" in hover


# --- states ---------------------------------------------------------------------------


def test_empty_results_name_what_was_asked_for_and_offer_one_reset() -> None:
    html = _get(query=" dxd ", genres=5)

    assert "По запросу «dxd» и выбранным фильтрам тайтлов нет." in html
    assert 'class="catalog-empty__reset" href="/catalog">Сбросить поиск и фильтры</a>' in html


def test_empty_search_alone_names_just_the_search() -> None:
    assert "По запросу «zzz» тайтлов нет." in _get(query="zzz")


def test_next_page_skeleton_and_error_plate_follow_the_design() -> None:
    script = (ROOT / "app/static/js/catalog-scroll.js").read_text(encoding="utf-8")
    assert 'class="catalog-card catalog-grid__skeleton" style="--i: ${i}"' in script
    assert "Уже загруженные тайтлы останутся на месте" in script
    assert "animation-delay: calc(var(--i, 0) * 80ms);" in CSS
    assert "border-top-color: #3dd6c3;" in CSS


def test_a_non_default_sort_gets_the_active_edge() -> None:
    assert "catalog-toolbar__sort catalog-toolbar__sort--active" in _get(sort="views")
    assert "catalog-toolbar__sort--active" not in _get()


def test_noise_is_a_static_file_not_a_data_uri() -> None:
    # img-src is 'self' only (app/security_headers.py) - a data: URI would be blocked.
    noise = _desktop_rule(".wn-catalog::before")
    assert 'url("../img/catalog-noise.svg")' in noise
    assert "data:" not in noise
    assert (ROOT / "app/static/img/catalog-noise.svg").is_file()

"""PR 296: the desktop catalog screen (CatalogDesktop.dc.html / Catalog handoff.md)."""

import re
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from ranobelib import CatalogPage
from ranobelib.models import Cover, Genre, Label, Title

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


# --- CatalogCard variant="cinema" ---------------------------------------------------


def _card_html(title: Title) -> str:
    fake = _FakeCatalog(CatalogPage(items=[title], page=1, has_next_page=False))
    with patch("app.services.catalog.Catalog", return_value=fake):
        html = client.get("/catalog", params={"sort": "views"}).text
    return html.split('<div class="catalog-card">', 1)[1].split("</button>\n</div>", 1)[0]


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
        "chapter_count": 1133,
    }
    fields.update(overrides)
    return Title(**fields)


def test_card_shows_title_genre_status_and_chapter_count() -> None:
    card = _card_html(_title())

    assert 'href="/titles/7--dragon" title="Последнее пламя в долгой ночи"' in card
    assert '<span class="catalog-card__title">Последнее пламя в долгой ночи</span>' in card
    assert '<span class="catalog-card__meta">Тёмное фэнтези · Онгоинг</span>' in card
    assert '<span class="catalog-card__sub">1 133 гл.</span>' in card
    assert 'class="catalog-card__glow" src="https://example.com/c.jpg"' in card
    assert 'data-role="title-quickview-trigger"' in card


def test_card_without_genres_chapters_or_cover_drops_those_lines() -> None:
    card = _card_html(_title(genres=[], chapter_count=None, cover=Cover()))

    assert '<span class="catalog-card__meta">Онгоинг</span>' in card
    assert "catalog-card__sub" not in card
    assert "catalog-card__glow" not in card
    assert "<img" not in card


def test_cinema_hover_follows_the_design() -> None:
    glow = _desktop_rule(".wn-catalog .catalog-card__glow")
    assert "filter: blur(26px) saturate(1.4);" in glow
    assert "opacity: 0;" in glow
    hover = CSS.split("@media (min-width: 768px) and (hover: hover) {", 1)[1]
    assert ".wn-catalog .catalog-card:hover {\n    transform: translateY(-4px);" in hover
    assert "opacity: 0.55;" in hover
    assert "transform: scale(1.03);" in hover


# --- FeaturedCard desktop -----------------------------------------------------------


def _featured_html(top: list[Title]) -> list[str]:
    fake = _FakeCatalog(
        CatalogPage(items=_titles(1, 30), page=1, has_next_page=False),
        featured=[CatalogPage(items=top, page=1, has_next_page=False)],
    )
    with patch("app.services.catalog.Catalog", return_value=fake):
        html = client.get("/catalog").text
    return html.split('<article\n  class="catalog-featured')[1:]


def test_featured_card_carries_the_design_content() -> None:
    top = _title(
        id=1001,
        slug_url="1001--prince",
        rus_name="Я стал наследным принцем Франции",
        summary="Историк просыпается в теле дофина.",
        genres=[Genre(id=i, name=f"Жанр {i}") for i in range(1, 6)],
        chapter_count=812,
    )
    first = _featured_html([top, _title(id=1002, slug_url="1002--x")])[0]

    assert 'aria-label="Популярно на RanobeLib: Я стал наследным принцем Франции"' in first
    assert "Один из самых просматриваемых" in first
    assert '<h3 class="catalog-featured__name">Я стал наследным принцем Франции</h3>' in first
    assert '<p class="catalog-featured__facts">Онгоинг · 812 глав</p>' in first
    assert "Историк просыпается в теле дофина." in first
    assert first.count("<li>Жанр") == 3
    assert 'class="catalog-featured__open" href="/titles/1001--prince"' in first
    assert '<span class="catalog-featured__issue-number">01</span>' in first
    assert "В библиотеку" not in first  # PR 298


def test_even_inserts_flip_the_cover_to_the_right() -> None:
    top = [_title(id=1000 + i, slug_url=f"{1000 + i}--t") for i in range(1, 4)]
    fake = _FakeCatalog(
        CatalogPage(items=_titles(1, 36), page=1, has_next_page=False),
        featured=[CatalogPage(items=top, page=1, has_next_page=False)],
    )
    with patch("app.services.catalog.Catalog", return_value=fake):
        html = client.get("/catalog").text

    classes = re.findall(r'class="(catalog-featured(?: catalog-featured--flip)?)"', html)
    flip = "catalog-featured catalog-featured--flip"
    assert classes == ["catalog-featured", flip, "catalog-featured"]


def test_chapter_plurals_on_the_featured_card() -> None:
    cases = [
        (1, "1 глава"),
        (3, "3 главы"),
        (11, "11 глав"),
        (22, "22 главы"),
        (1205, "1 205 глав"),
    ]
    for count, word in cases:
        card = _featured_html([_title(id=1001, slug_url="1001--x", chapter_count=count)])[0]
        assert f"Онгоинг · {word}</p>" in card, count


def test_featured_desktop_layers_follow_the_design() -> None:
    card = _desktop_rule(".wn-catalog .catalog-featured__card")
    assert "min-height: 340px;" in card
    assert "border-radius: 20px;" in card
    backdrop = _desktop_rule(".wn-catalog .catalog-featured__backdrop")
    assert "filter: blur(60px) saturate(1.05) brightness(0.62);" in backdrop
    halo = _desktop_rule(".wn-catalog .catalog-featured__halo")
    assert "filter: blur(90px) saturate(1.3);" in halo and "opacity: 0.32;" in halo
    assert "flex-direction: row-reverse;" in _desktop_rule(
        ".wn-catalog .catalog-featured--flip .catalog-featured__inner"
    )
    assert "800 36px/1.12" in _desktop_rule(".wn-catalog .catalog-featured__name")

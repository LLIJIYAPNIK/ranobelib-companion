"""«Разделы» - the catalog page (catalog.html, catalog-spotlight.js): the sections over a
wall of covers, the lamp beside it (/titles/{slug}/lamp), the genre bar, and on phones
the strip, three to a row and the title's bottom sheet."""

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib import CatalogPage
from ranobelib.models import Country, Cover, Genre, Label, Title

from app.main import app
from tests.test_api_catalog import _FakeCatalog, _titles

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
SCRIPT = (ROOT / "app/static/js/catalog-spotlight.js").read_text(encoding="utf-8")
STATUSES = [Label(id=1, label="Онгоинг"), Label(id=2, label="Завершён")]
GENRES = [Genre(id=5, name="Фэнтези"), Genre(id=8, name="Драма")]
client = TestClient(app)


def _get(params: dict[str, object] | None = None, *, items: int = 3, statuses=STATUSES):
    fake = _FakeCatalog(
        CatalogPage(items=_titles(1, items), page=1, has_next_page=True),
        genres=GENRES,
        statuses=statuses,
    )
    with patch("app.services.catalog.Catalog", return_value=fake):
        response = client.get("/catalog", params=params or {})
    return fake, response.text


def _rule(selector: str, *, phone: bool = False) -> str:
    """The last rule for `selector` in the «Разделы» part of app.css - in its phone block
    when `phone`."""
    block = CSS.split("«Разделы» - the catalog page", 1)[1]
    desktop, mobile = block.split("@media (max-width: 767px)", 1)
    mobile = mobile.split("@media (prefers-reduced-motion", 1)[0]
    rules = re.findall(
        rf"\n{'  ' if phone else ''}{re.escape(selector)} \{{([^}}]*)\}}",
        mobile if phone else desktop,
    )
    assert rules, selector
    return rules[-1]


# --- The sections -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("section", "sort", "statuses", "min_chapters"),
    [
        (None, "last_chapter_at", None, None),
        ("fresh", "last_chapter_at", None, None),
        ("new", "created_at", None, None),
        ("done", "views", [2], None),
        ("long", "chap_count", None, 500),
        ("nonsense", "last_chapter_at", None, None),
    ],
)
def test_each_section_is_its_own_cut_of_the_listing(
    section: str | None, sort: str, statuses: list[int] | None, min_chapters: int | None
) -> None:
    fake, html = _get({"section": section} if section else {})

    assert fake.received_kwargs["sort"] == sort
    assert fake.received_kwargs["statuses"] == statuses
    assert fake.received_kwargs["min_chapters"] == min_chapters
    assert len(fake.calls) == 1  # one listing - no featured inserts any more
    assert 'data-mode="editorial"' in html
    # The next pages list the same cut.
    assert f'data-sort="{sort}"' in html
    assert f'data-statuses="{",".join(map(str, statuses or []))}"' in html
    assert f'data-min-chapters="{min_chapters or ""}"' in html


def test_the_tabs_are_links_and_the_current_one_is_marked() -> None:
    _, html = _get({"section": "done"})

    tabs = re.findall(r'<a class="cg-sections__tab" href="([^"]+)"( aria-current="page")?', html)
    assert tabs == [
        ("/catalog", ""),
        ("/catalog?section=new", ""),
        ("/catalog?section=done", ' aria-current="page"'),
        ("/catalog?section=long", ""),
    ]
    assert "<b>Завершённые</b><small>можно читать до конца</small>" in html
    assert "<b>Надолго</b><small>от 500 глав</small>" in html


def test_finished_is_found_by_its_label_or_not_offered() -> None:
    fake, html = _get({"section": "done"}, statuses=[Label(id=1, label="Онгоинг")])

    assert "section=done" not in html
    assert fake.received_kwargs["sort"] == "last_chapter_at"  # back to the default


def test_asking_anything_leaves_the_section_behind() -> None:
    fake, html = _get({"section": "long", "query": "dxd"})

    assert 'data-mode="results"' in html
    assert 'class="cg-sections"' not in html
    assert fake.received_kwargs["sort"] == "last_chapter_at"
    assert fake.received_kwargs["min_chapters"] is None
    assert 'data-role="catalog-criteria"' in html


def test_the_sort_shows_the_sections_own() -> None:
    _, html = _get({"section": "new"})

    assert '<option value="created_at" selected>По дате добавления</option>' in html
    assert 'aria-label="Сортировка: По дате добавления"' in html
    assert '<input type="radio" name="sort" value="created_at" checked>' in html


def test_the_noscript_next_page_keeps_the_section() -> None:
    _, html = _get({"section": "new"})

    assert "&section=new&page=2" in html


# --- The phone's strip ---------------------------------------------------------------


def test_a_section_with_more_than_eight_opens_with_the_strip() -> None:
    _, html = _get(items=12)

    strip = html.split('data-role="catalog-stage"', 1)[1].split('<h3 class="cg-more">', 1)[0]
    assert strip.count('data-role="catalog-stage-book"') == 8
    assert 'class="hm-cover cg-stage__book is-mid" href="/titles/1--test-novel-1"' in strip
    assert 'data-name="Novel 1" data-facts="Онгоинг"' in strip
    assert '<span class="cg-stage__pos hm-num" data-role="catalog-stage-pos">1 / 8</span>' in strip
    assert "cg--staged" in html
    assert '<h3 class="cg-more">Ещё в разделе</h3>' in html


def test_no_strip_for_a_short_section_or_for_results() -> None:
    assert 'data-role="catalog-stage"' not in _get(items=8)[1]
    assert 'data-role="catalog-stage"' not in _get({"query": "dxd"}, items=12)[1]


# --- The genre bar and «Случайный тайтл» ---------------------------------------------


def test_each_genre_adds_or_drops_itself_and_keeps_the_rest() -> None:
    _, html = _get({"query": "dxd", "genres": 5, "sort": "views"})

    bar = html.split('data-role="catalog-genre-bar"', 1)[1].split("</nav>", 1)[0]
    assert (
        '<a class="cg-chip" href="/catalog?sort=views&amp;query=dxd" aria-current="true"'
        ' draggable="false">Фэнтези</a>'
    ) in bar
    assert 'href="/catalog?sort=views&amp;query=dxd&amp;genres=5&amp;genres=8"' in bar
    assert 'data-role="catalog-genre-bar-all">Все жанры<span class="hm-num">2</span>' in bar
    assert "data-no-tab-swipe" in bar


def test_random_title_keeps_what_was_asked_for() -> None:
    _, html = _get({"genres": 5, "statuses": 2})

    assert 'class="catalog-toolbar__random" href="/catalog/random?genres=5&amp;statuses=2"' in html
    assert 'class="catalog-toolbar__random" href="/catalog/random"' in _get()[1]


# --- The card ----------------------------------------------------------------------------


def test_the_card_is_what_the_lamp_and_the_sheet_find() -> None:
    title = Title(
        id=7,
        name="Dragon",
        slug="dragon",
        slug_url="7--dragon",
        cover=Cover(default="https://example.com/c.jpg"),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=2, label="Завершён"),
        country=Country(id=11, name="Корея"),
    )
    fake = _FakeCatalog(CatalogPage(items=[title], page=1, has_next_page=False))
    with patch("app.services.catalog.Catalog", return_value=fake):
        html = client.get("/catalog").text

    assert '<div class="catalog-card" data-role="catalog-card" data-slug-url="7--dragon">' in html
    assert '<span class="catalog-card__meta">Завершён · Корея</span>' in html
    assert 'data-role="catalog-lamp" aria-label="О тайтле" aria-live="polite" hidden' in html


# --- The lamp ----------------------------------------------------------------------------


class _Lib:
    def __init__(self, title: Title) -> None:
        self.title = title

    async def __aenter__(self) -> "_Lib":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get_info(self) -> Title:
        return self.title


def test_the_lamp_says_what_the_card_cant() -> None:
    title = Title(
        id=7,
        name="Dragon",
        rus_name="Последнее пламя",
        slug="dragon",
        slug_url="7--dragon",
        cover=Cover(default="https://example.com/c.jpg"),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
        country=Country(id=1, name="Китай"),
        summary="Мир, где ночь не кончается.",
        genres=[Genre(id=i, name=f"Жанр {i}") for i in range(1, 8)],
        chapter_count=615,
    )
    with patch("app.services.client.RanobeLib", return_value=_Lib(title)):
        response = client.get("/titles/7--dragon/lamp")

    html = response.text
    assert response.status_code == 200
    assert "<!doctype" not in html.lower()  # a fragment
    assert 'data-slug-url="7--dragon" data-cover="https://example.com/c.jpg"' in html
    assert "Онгоинг · Китай · 16+<br><b>615 глав</b>" in html
    assert html.count("<li>Жанр") == 5
    assert '<h3 class="cg-lamp__name">Последнее пламя</h3>' in html
    assert "Мир, где ночь не кончается." in html
    assert 'class="hm-go" href="/titles/7--dragon"' in html
    # A guest gets the way in, not a toggle.
    assert 'class="cg-lamp__lib" href="/login"' in html


def test_the_lamp_lights_on_a_pause_and_fetches_once_per_title() -> None:
    assert "const HOVER_INTENT_MS = 90;" in SCRIPT
    assert "fetch(`/titles/${encodeURIComponent(slug)}/lamp`)" in SCRIPT
    assert "kept.set(slug, request);" in SCRIPT
    assert (
        'document.addEventListener("catalog:library", (event) => kept.delete(event.detail.slug));'
        in SCRIPT
    )
    assert 'grid.classList.add("is-pointing");' in SCRIPT
    assert 'window.matchMedia("(min-width: 1024px)")' in SCRIPT
    assert "opacity: 0.45;" in _rule(
        ".hm--catalog .catalog-grid.is-pointing > .catalog-card:not(.is-lit)"
    )
    assert "position: sticky;" in _rule(".cg-lamp")


def test_every_toggle_of_a_title_flips_together() -> None:
    toggle = (ROOT / "app/static/js/catalog-library-toggle.js").read_text(encoding="utf-8")
    assert "function setAll(slug, on)" in toggle
    assert 'new CustomEvent("catalog:library", { detail: { slug, on } })' in toggle


# --- Phones ------------------------------------------------------------------------------


def test_on_phones_a_tap_opens_the_sheet_and_the_strip_follows_the_swipe() -> None:
    assert 'window.bottomSheet.open({ title: "О тайтле", content: sheet, opener });' in SCRIPT
    assert "frame = window.requestAnimationFrame(() => {" in SCRIPT
    assert "middle();" in SCRIPT
    strip = _rule(".cg-stage__row", phone=True)
    assert "scroll-snap-type: x mandatory;" in strip
    assert "padding: 0 calc(50% - var(--w) / 2) 4px;" in strip


def test_on_phones_no_plus_on_the_covers_and_three_to_a_row() -> None:
    assert "display: none;" in _rule(".hm--catalog .catalog-card__action", phone=True)
    grid = _rule('.hm--catalog[data-mode="editorial"] .catalog-grid', phone=True)
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in grid
    assert "display: none;" in _rule(
        ".cg--staged .catalog-grid > .catalog-card:nth-child(-n + 8)", phone=True
    )
    # The sheet shows the lamp whole - its story clamped, nothing to scroll.
    assert "-webkit-line-clamp: 4;" in _rule(".cg-sheet .cg-lamp__summary", phone=True)

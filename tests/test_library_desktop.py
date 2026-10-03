"""PR 297: the desktop library screen (LibraryDesktop.dc.html / Catalog handoff.md)."""

import re
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.connection import connection
from app.db.library import record_progress
from tests.auth_helpers import register
from tests.db_reset import reset_app_database
from tests.test_api_library import _fake_title, _FakeClient

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def _library_rule(selector: str) -> str:
    """The body of the last rule in the LibraryDesktop part of app.css (and the shared
    catalog shell above it) whose selector list includes `selector`."""
    block = CSS.split("CatalogDesktop (PR 296", 1)[1]
    matches = [
        body
        for selectors, body in re.findall(r"\n  ([^{}@/]+?) \{([^}]*)\}", block)
        if selector in [part.strip() for part in selectors.split(",")]
    ]
    assert matches, selector
    return matches[-1]


def _add(client: TestClient, *slugs: str) -> None:
    for slug in slugs:
        title = _fake_title(slug_url=slug)
        with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
            client.post(f"/library/{slug}/add")


def _library(client: TestClient) -> str:
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        return client.get("/library").text


@pytest.mark.parametrize(
    ("count", "word"), [(1, "1 тайтл"), (3, "3 тайтла"), (5, "5 тайтлов")]
)
def test_header_card_counts_the_titles(client: TestClient, count: int, word: str) -> None:
    register(client, "alice@example.com")
    _add(client, *[f"{i}--novel" for i in range(1, count + 1)])

    html = _library(client)

    assert f'<span class="wn-library__eyebrow">Ваши тайтлы · {word}</span>' in html


def test_header_card_holds_the_switch_and_the_add_form(client: TestClient) -> None:
    register(client, "alice@example.com")
    html = _library(client)

    head = html.split('<section class="wn-library__head">', 1)[1].split("</section>", 1)[0]
    assert '<h1 class="wn-library__title">Библиотека</h1>' in head
    side = head.split('<div class="wn-library__head-side">', 1)[1]
    assert 'data-role="library-switch"' in side
    assert 'action="/library/add"' in side
    assert 'placeholder="Ссылка на тайтл ranobelib.me"' in side


def test_add_error_stays_inside_the_header_card(client: TestClient) -> None:
    register(client, "alice@example.com")
    response = client.post("/library/add", data={"url": "not a link"})

    head = response.text.split('<section class="wn-library__head">', 1)[1].split("</section>", 1)[0]
    assert 'id="library-add-error"' in head


def test_guest_header_has_no_count(client: TestClient) -> None:
    assert "wn-library__eyebrow" not in client.get("/library").text


def test_toolbar_is_the_shared_sticky_glass_panel() -> None:
    # The glass panel itself is shared with the catalog (one rule for both)...
    shared = CSS.split("  .wn-catalog .catalog-toolbar,\n  .wn-library .wn-library-toolbar {", 1)[1]
    shared = shared.split("}", 1)[0]
    assert "margin: -44px auto 0;" in shared
    assert "backdrop-filter: blur(18px) saturate(1.2);" in shared
    # ...the library's own rules make it sticky and size its controls.
    assert "position: sticky;" in _library_rule(".wn-library .wn-library-toolbar")
    search = _library_rule(".wn-library .wn-library-search input")
    assert "height: var(--wn-library-ctl-h);" in search


def test_add_button_is_glass_not_the_purple_cta() -> None:
    button = _library_rule(".wn-library .wn-library__add .wn-btn")
    assert "background: rgb(255 255 255 / 6%);" in button
    assert "height: 44px;" in button


# --- «Продолжить чтение» -----------------------------------------------------------


def _hero(html: str) -> str:
    return html.split('data-role="library-hero"', 1)[1].split("</section>", 1)[0]


async def test_hero_continues_the_most_recently_read_title(client: TestClient) -> None:
    register(client, "alice@example.com")
    _add(client, "1--first", "2--second", "3--never-opened")
    async with connection() as conn:
        await record_progress(conn, 1, "1--first", "1", "4")
        await record_progress(conn, 1, "2--second", "11", "92")  # read last

    html = _library(client)
    hero = _hero(html)

    assert "Продолжить чтение" in hero
    assert "Читали сегодня" in hero
    assert '<a href="/titles/2--second">' in hero
    assert "Том 11 · Глава 92" in hero
    assert 'href="/titles/2--second/chapters/11/92"' in hero
    assert "Продолжить · Гл. 92" in hero
    assert 'href="/titles/2--second#title-panel-toc"' in hero
    # Its own card in the list carries data-hero (hidden while the hero is shown).
    card = re.search(
        r'<article\s+class="wn-library-card"\s+data-role="library-item" data-hero[^>]*>', html
    )
    assert card is not None and 'data-name="test novel"' in card.group(0)
    assert html.count(" data-hero") == 1


async def test_no_hero_when_nothing_was_opened_yet(client: TestClient) -> None:
    register(client, "alice@example.com")
    _add(client, "1--first")

    html = _library(client)

    assert 'data-role="library-hero"' not in html
    assert " data-hero" not in html


async def test_hero_progress_column_only_with_a_known_percent(client: TestClient) -> None:
    register(client, "alice@example.com")
    _add(client, "1--first")
    async with connection() as conn:
        await record_progress(conn, 1, "1--first", "1", "4")

    with patch("app.api.library.reading_progress_percent", return_value=41):
        with_pct = _hero(_library(client))
    with patch("app.api.library.reading_progress_percent", return_value=None):
        without = _hero(_library(client))

    assert '<span class="wn-library-hero__pct">41<span>%</span></span>' in with_pct
    assert 'aria-valuenow="41"' in with_pct
    assert "Остановились на главе 4" in with_pct
    assert "wn-library-hero__progress" not in without


def test_hero_design_values() -> None:
    assert "min-height: 320px;" in _library_rule(".wn-library-hero__card")
    assert "width: 176px;" in _library_rule(".wn-library-hero__cover")
    assert "width: 280px;" in _library_rule(".wn-library-hero__progress")
    assert "font-size: 72px;" in _library_rule(".wn-library-hero__pct")
    assert "800 36px/1.12" in _library_rule(".wn-library-hero__title")


# --- «Моя библиотека» ---------------------------------------------------------------


async def test_one_grid_of_every_title_ending_in_the_add_tile(client: TestClient) -> None:
    register(client, "alice@example.com")
    _add(client, "1--first", "2--second", "3--third")
    async with connection() as conn:
        await record_progress(conn, 1, "2--second", "1", "3")

    html = _library(client)
    grid = html.split('data-role="library-list"', 1)[1].split("</section>", 1)[0]

    assert grid.count('data-role="library-item"') == 3
    assert grid.count("wn-library-card--new") == 2
    # The tile is the grid's last cell.
    assert grid.rfind('data-role="library-add-tile"') > grid.rfind('data-role="library-item"')
    assert 'href="/catalog" data-role="library-add-tile"' in grid
    assert "Добавить из каталога" in grid
    shelf_title = '<h2 class="wn-library-shelf__title" id="library-shelf-title">'
    assert f"{shelf_title}Моя библиотека</h2>" in html
    assert 'data-role="library-rest">ещё 2 тайтла</span>' in html
    assert 'data-role="library-end-text">Это вся библиотека · 3 тайтла</span>' in html
    assert 'href="/catalog" data-role="library-end-catalog">Найти новое в каталоге →</a>' in html


async def test_shelf_counts_everything_without_a_hero(client: TestClient) -> None:
    register(client, "alice@example.com")
    _add(client, "1--first", "2--second")

    assert 'data-role="library-rest">2 тайтла</span>' in _library(client)


async def test_shelf_with_only_the_hero_title(client: TestClient) -> None:
    register(client, "alice@example.com")
    _add(client, "1--first")
    async with connection() as conn:
        await record_progress(conn, 1, "1--first", "1", "3")

    assert 'data-role="library-rest">пока только этот</span>' in _library(client)


def test_toolbar_keeps_the_add_tile_last_when_sorting() -> None:
    script = (ROOT / "app/static/js/library-toolbar.js").read_text(encoding="utf-8")
    assert "const tile = list.querySelector('[data-role=\"library-add-tile\"]');" in script
    assert "if (tile) list.append(tile);" in script


def test_shelf_design_values() -> None:
    tile = CSS.split(".wn-library-add-tile {", 1)[1].split("}", 1)[0]
    assert "border: 1px dashed rgb(255 255 255 / 14%);" in tile
    assert "min-height: 200px;" in tile
    assert "gap: 20px;" in _library_rule(".wn-library .wn-library-grid")


# --- results mode -------------------------------------------------------------------


async def test_filters_follow_the_handoff(client: TestClient) -> None:
    register(client, "alice@example.com")
    _add(client, "1--first")
    html = _library(client)

    progress = html.split('data-role="library-progress"', 1)[1].split("</select>", 1)[0]
    assert re.findall(r'<option value="([^"]+)">([^<]+)</option>', progress) == [
        ("any", "Любой"),
        ("started", "Начатые"),
        ("new", "Не начатые"),
        ("done", "Дочитанные"),
    ]
    sort = html.split('data-role="library-sort"', 1)[1].split("</select>", 1)[0]
    assert re.findall(r'<option value="[^"]+">([^<]+)</option>', sort) == [
        "Недавно читал",
        "Недавно добавленные",
        "По названию",
        "По прогрессу",
    ]


async def test_results_mode_hooks_are_in_place(client: TestClient) -> None:
    register(client, "alice@example.com")
    _add(client, "1--first")
    html = _library(client)

    assert 'class="library-page wn-library" data-mode="default"' in html
    criteria = html.split('data-role="library-criteria"', 1)[1]
    criteria = criteria.split("</div>\n        </div>", 1)[0]
    assert criteria.startswith(" hidden>")
    assert 'data-role="library-criteria-chips"' in criteria
    assert ">Сбросить всё</button>" in criteria
    assert 'data-role="library-criteria-count"' in criteria
    assert 'data-role="library-end-reset" hidden>Сбросить условия</button>' in html


def test_toolbar_script_switches_the_modes() -> None:
    script = (ROOT / "app/static/js/library-toolbar.js").read_text(encoding="utf-8")
    assert 'page.dataset.mode = results ? "results" : "default";' in script
    assert "sort.selectedIndex !== 0 || progress.selectedIndex !== 0" in script
    assert '`${titlesWord(visibleTotal)} из ${total}`' in script
    assert '"Больше ничего не подходит под условия"' in script
    assert 'if (value === "done") return pct >= 100;' in script


def test_results_mode_hides_the_default_blocks() -> None:
    hidden = CSS.split('.wn-library[data-mode="results"] [data-role="library-hero"],', 1)[1]
    hidden = hidden.split("}", 1)[0]
    for hook in ("library-add-tile", "wn-library-shelf__head", "library-end-catalog"):
        assert hook in hidden
    assert "display: none;" in hidden
    # ...and the hero's own card is hidden only in the default mode.
    assert '.wn-library[data-mode="default"] [data-hero] {\n  display: none;' in CSS


# --- states ---------------------------------------------------------------------------


async def test_nothing_found_card_is_ready_and_hidden(client: TestClient) -> None:
    register(client, "alice@example.com")
    _add(client, "1--first")
    html = _library(client)

    empty = html.split('data-role="library-no-results"', 1)[1].split("</div>\n      </div>", 1)[0]
    assert empty.startswith(' role="status" hidden>')
    assert '<h2 class="catalog-empty__title">В библиотеке ничего не нашлось</h2>' in empty
    assert ">Сбросить условия</button>" in empty
    assert 'href="/catalog" data-role="library-no-results-catalog">Искать в каталоге →</a>' in empty


def test_nothing_found_names_the_search_and_carries_it_to_the_catalog() -> None:
    script = (ROOT / "app/static/js/library-toolbar.js").read_text(encoding="utf-8")
    assert "`Среди ваших тайтлов нет «${query}». Возможно, он ещё не добавлен" in script
    assert "`/catalog?${new URLSearchParams({ query })}`" in script
    assert "if (end) end.hidden = visibleTotal === 0;" in script


async def test_empty_library_keeps_its_own_message(client: TestClient) -> None:
    register(client, "alice@example.com")

    html = _library(client)

    assert "Пока пусто." in html
    assert 'data-role="library-hero"' not in html

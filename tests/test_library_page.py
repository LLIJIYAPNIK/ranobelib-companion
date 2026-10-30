"""«Полки» - the library page (library.html, library-shelves.js): the home's «Витрина»
once per shelf. Which shelf a title lands on is tests/test_library_shelves.py."""

import re
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib.models import Chapter, Volume

from app.config import get_settings
from app.db.connection import connection
from app.db.library import record_progress
from tests.auth_helpers import register
from tests.db_reset import reset_app_database
from tests.test_api_library import _fake_title, _FakeClient

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
PAGE = (ROOT / "app/templates/library.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "app/static/js/library-shelves.js").read_text(encoding="utf-8")
# Three chapters: 1, 2 and 3 of volume 1.
VOLUMES = [
    Volume(number="1", chapters=[Chapter(id=i, volume="1", number=str(i)) for i in (1, 2, 3)])
]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def _sdk() -> object:
    return patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title(), VOLUMES))


def _add(client: TestClient, *slugs: str) -> None:
    for slug in slugs:
        with patch(
            "app.services.client.RanobeLib", return_value=_FakeClient(_fake_title(slug_url=slug))
        ):
            client.post(f"/library/{slug}/add")


def _library(client: TestClient) -> str:
    with _sdk():
        return client.get("/library").text


def _shelf(html: str, key: str) -> str:
    return html.split(f'data-shelf="{key}"', 1)[1].split("</section>", 1)[0]


def _css_rule(selector: str, *, phone: bool = False) -> str:
    """The last rule for `selector` in the «Полки» part of app.css - in its phone block
    when `phone`."""
    block = CSS.split("«Полки» - the library page", 1)[1]
    desktop, mobile = block.split("@media (max-width: 767px)", 1)
    mobile = mobile.split("@media (prefers-reduced-motion", 1)[0]
    rules = re.findall(
        rf"\n{'  ' if phone else ''}{re.escape(selector)} \{{([^}}]*)\}}",
        mobile if phone else desktop,
    )
    assert rules, selector
    return rules[-1]


# --- The page ---------------------------------------------------------------------


@pytest.mark.parametrize(("count", "word"), [(1, "1 тайтл"), (3, "3 тайтла"), (5, "5 тайтлов")])
def test_header_counts_the_titles(client: TestClient, count: int, word: str) -> None:
    register(client, "alice@example.com")
    _add(client, *[f"{i}--novel" for i in range(1, count + 1)])

    html = _library(client)

    assert f'<span class="lb-head__count hm-num" data-role="library-total">{word}</span>' in html


def test_header_is_the_homes_with_the_switch_and_catalog_search(client: TestClient) -> None:
    register(client, "alice@example.com")
    html = _library(client)

    assert '<main class="hm hm--library library-tabs-content" data-role="library-page">' in html
    head = html.split('<header class="hm-head lb-head hm-rise">', 1)[1].split("</header>", 1)[0]
    assert '<h1 class="hm-title" id="library-title">Библиотека</h1>' in head
    assert 'data-role="library-switch"' in head
    assert '<form class="hm-search" method="get" action="/catalog" role="search"' in head


def test_guest_gets_the_locked_state_and_no_count(client: TestClient) -> None:
    html = client.get("/library").text

    assert 'data-role="locked-feature"' in html
    assert 'data-role="library-total"' not in html
    assert 'data-role="library-titles"' not in html


def test_empty_library_keeps_its_own_message(client: TestClient) -> None:
    register(client, "alice@example.com")
    html = _library(client)

    assert "Пока пусто." in html
    assert 'href="/catalog">Открыть каталог</a>' in html
    assert 'data-shelf="reading"' not in html


async def test_three_shelves_each_with_its_way_in(client: TestClient) -> None:
    register(client, "alice@example.com")
    _add(client, "1--reading", "2--new", "3--done")
    async with connection() as conn:
        await record_progress(conn, 1, "1--reading", "1", "2")
        await record_progress(conn, 1, "3--done", "1", "3")

    html = _library(client)

    assert re.findall(r'<h2 class="hm-h2" id="shelf-\w+-title">([^<]+)</h2>', html) == [
        "Читаю",
        "Не начаты",
        "Дочитаны",
    ]
    reading = _shelf(html, "reading")
    assert 'data-slug-url="1--reading"' in reading
    assert "Том 1 · Глава 2 из 3 · осталась <b>1 глава</b>" in reading
    assert 'href="/titles/1--reading/chapters/1/2"' in reading
    assert '<span class="hm-go__label">Читать дальше<small>Глава 2</small></span>' in reading
    assert 'aria-label="Прочитано 67%"' in reading

    new = _shelf(html, "new")
    assert 'data-slug-url="2--new"' in new
    assert "Онгоинг · 3 главы<br>Добавлен сегодня" in new
    assert 'href="/titles/2--new/chapters/1/1"' in new
    assert '<span class="hm-go__label">Начать читать<small>Глава 1</small></span>' in new

    done = _shelf(html, "done")
    assert 'data-slug-url="3--done"' in done
    assert "Дочитано · 3 главы" in done
    assert '<span class="hm-go__label">Перечитать<small>с первой главы</small></span>' in done
    assert 'href="/titles/3--done/chapters/1/1"' in done


async def test_each_cover_and_its_details_go_and_come_back_together(client: TestClient) -> None:
    # library-card-actions.js removes every [data-role="library-item"] of a title.
    register(client, "alice@example.com")
    _add(client, "1--first", "2--second")

    html = _library(client)

    for slug in ("1--first", "2--second"):
        assert html.count(f'data-role="library-item" data-slug-url="{slug}"') == 2
    shelf = _shelf(html, "new")
    # Without script: the first book in front and its details; the rest are links.
    assert shelf.count('class="lb-book is-active"') == 1
    assert shelf.count('class="lb-panel is-active"') == 1
    assert shelf.count("data-panel hidden") == 1
    # The ⋯ of the shared menu sits in the details.
    assert shelf.count('data-role="library-more"') == 2


def test_the_shelves_are_the_homes_pieces() -> None:
    imports = "import search_form, cover_progress, progress_line, go_button"
    assert '{% from "home/_macros.html" ' + imports + " %}" in PAGE
    assert '<span class="hm-cover lb-book__cover">' in PAGE
    assert '<img class="hl-art lb-shelf__art"' in PAGE
    assert '<img class="hm-ambient"' in PAGE


def test_scripts_are_wired_and_the_controls_wait_for_them(client: TestClient) -> None:
    register(client, "alice@example.com")
    _add(client, "1--first")
    html = _library(client)

    scripts = ("library-shelves", "library-card-actions", "library-tabs-swipe", "library-switch")
    for script in scripts:
        assert f"static/js/{script}.js" in html
    assert "library-toolbar.js" not in html
    assert 'data-role="library-tools" hidden' in html
    assert re.findall(r'data-shelf-tab="(\w+)"', html) == ["all", "reading", "new", "done"]
    assert re.findall(r'data-sort="(\w+)"', html) == ["recent", "added", "name", "progress"]
    # The sort is a styled list, not a native <select>.
    tools = html.split('data-role="library-tools"', 1)[1].split('<div class="lb-shelves">', 1)[0]
    assert "<select" not in tools
    assert 'role="listbox"' in tools


# --- Behaviour (library-shelves.js) ------------------------------------------------


def test_arrows_show_only_where_there_is_somewhere_to_go() -> None:
    assert "nav.hidden = fits;" in SCRIPT
    assert "back.hidden = row.scrollLeft < 4;" in SCRIPT
    assert "forward.hidden = row.scrollLeft + row.clientWidth > row.scrollWidth - 4;" in SCRIPT
    assert "display: none;" in _css_rule(".lb-shelf__nav [hidden]")


def test_a_shelf_is_held_and_dragged_and_a_drag_is_not_a_click() -> None:
    assert 'event.pointerType !== "mouse"' in SCRIPT
    assert "row.setPointerCapture(drag.id);" in SCRIPT
    assert 'row.addEventListener("click", swallow, { capture: true, once: true });' in SCRIPT
    assert "cursor: grab;" in _css_rule(".lb-shelf__row.can-scroll")
    assert "cursor: grabbing;" in _css_rule(".lb-shelf__row.is-dragging")
    assert 'draggable="false"' in PAGE


def test_on_a_phone_the_details_follow_the_middle_while_swiping() -> None:
    # Every frame of the scroll, not once it settles.
    assert "frame = requestAnimationFrame(() => {" in SCRIPT
    assert "middle();" in SCRIPT
    row = _css_rule(".lb-shelf__row", phone=True)
    assert "scroll-snap-type: x mandatory;" in row
    assert "padding: 0 calc(50% - var(--w) / 2) 4px;" in row
    assert "scroll-snap-align: center;" in _css_rule(".lb-book", phone=True)
    assert "0.15s" in _css_rule(".lb-shelf[data-live] .lb-panel", phone=True)
    assert "display: none;" in _css_rule(".lb-shelf__nav", phone=True)


def test_the_four_tabs_fit_one_row_on_a_phone() -> None:
    tabs = _css_rule(".lb-tabs", phone=True)
    assert "grid-template-columns: repeat(4, minmax(0, 1fr));" in tabs
    assert "flex-direction: column;" in _css_rule(".lb-tabs button", phone=True)


def test_the_shelves_recount_after_a_removal_or_its_undo() -> None:
    assert 'document.addEventListener("library:changed", () => render(false));' in SCRIPT


def test_the_sort_list_is_driven_from_the_keyboard() -> None:
    for key in ('"ArrowDown"', '"ArrowUp"', '"Enter"', '"Escape"'):
        assert key in SCRIPT
    assert "aria-expanded" in SCRIPT


def test_a_shelf_with_nothing_to_show_leaves_the_page() -> None:
    """Issue 396: emptied by the search or a removal, or not on this tab - hidden; the
    first shelf still shown is made the big one."""
    hide = 'section.hidden = list.length === 0 || (view.tab !== "all" && view.tab !== key);'
    assert hide in SCRIPT
    assert "shelf-empty" not in SCRIPT
    assert 'shelf.section.classList.toggle("lb-shelf--small", i > 0)' in SCRIPT
    assert "const lead = visible[0]?.first();" in SCRIPT


def test_an_empty_tab_or_search_says_why() -> None:
    assert "none.hidden = visible.length > 0;" in SCRIPT
    for copy in (
        "Дочитанных тайтлов пока нет.",
        "Все тайтлы в библиотеке уже начаты.",
        "Сейчас нет начатых тайтлов",
        "В библиотеке нет «${view.query}».",
        "На полке «${tabName(view.tab)}» нет «${view.query}».",
    ):
        assert copy in SCRIPT, copy
    # A tab whose shelf the server left out counts 0, not «undefined».
    assert "String(counts[tab.dataset.shelfTab] ?? 0)" in SCRIPT


def test_swiping_a_shelf_never_switches_to_the_catalog() -> None:
    swipe = (ROOT / "app/static/js/library-tabs-swipe.js").read_text(encoding="utf-8")
    assert 'if (event.target.closest("[data-no-tab-swipe]")) return;' in swipe
    assert 'data-role="shelf-row" data-no-tab-swipe' in PAGE


# --- The shared switch on phones (PR 294) ---------------------------------------------


def _phone_rule(selector: str) -> str:
    matches = re.findall(rf"\n  {re.escape(selector)} \{{([^}}]*)\}}", CSS)
    assert matches, selector
    return matches[-1]


def test_library_switch_spans_the_row_on_phones() -> None:
    assert '{% include "_library_switch.html" %}' in PAGE
    assert "js/ribbon.js" not in PAGE
    switch = _phone_rule(".wn-lib-switch")
    assert "display: grid;" in switch
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in switch
    assert "width: 100%;" in switch

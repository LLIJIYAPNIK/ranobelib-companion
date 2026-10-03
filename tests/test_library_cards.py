"""PR 298: LibraryCard - the ⋯ menu, the removal confirmation, «Вернуть» - and the
single «В библиотеку» action on catalog cards (LibraryCard.dc.html, CatalogCard.dc.html,
Catalog handoff.md)."""

import re
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib import CatalogPage

from app.config import get_settings
from app.db.connection import connection
from app.db.library import get_entry, record_progress
from tests.auth_helpers import register
from tests.db_reset import reset_app_database
from tests.test_api_catalog import _FakeCatalog, _titles
from tests.test_api_library import _fake_title, _FakeClient

ROOT = Path(__file__).resolve().parents[1]
JSON = {"Accept": "application/json"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def _add(client: TestClient, *slugs: str) -> None:
    for slug in slugs:
        with patch(
            "app.services.client.RanobeLib", return_value=_FakeClient(_fake_title(slug_url=slug))
        ):
            client.post(f"/library/{slug}/add")


def _library(client: TestClient) -> str:
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        return client.get("/library").text


# --- add/remove answer JSON to fetch() ------------------------------------------------


async def test_add_and_remove_answer_json_when_asked(client: TestClient) -> None:
    register(client, "alice@example.com")
    title = _fake_title(slug_url="1--first")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        added = client.post("/library/1--first/add", headers=JSON, follow_redirects=False)
    assert added.status_code == 200
    assert added.json() == {"in_library": True}
    async with connection() as conn:
        assert await get_entry(conn, 1, "1--first") is not None

    removed = client.post("/library/1--first/remove", headers=JSON, follow_redirects=False)
    assert removed.status_code == 200
    assert removed.json() == {"in_library": False}
    async with connection() as conn:
        assert await get_entry(conn, 1, "1--first") is None


def test_plain_form_posts_still_redirect(client: TestClient) -> None:
    register(client, "alice@example.com")
    title = _fake_title(slug_url="1--first")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        added = client.post("/library/1--first/add", follow_redirects=False)
    removed = client.post("/library/1--first/remove", follow_redirects=False)

    assert added.status_code == 303
    assert removed.status_code == 303
    assert removed.headers["location"] == "/library"


def test_guest_json_add_is_sent_to_login(client: TestClient) -> None:
    response = client.post("/library/1--first/add", headers=JSON, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# --- LibraryCard menu ---------------------------------------------------------------


async def test_card_has_the_menu_and_the_confirmation(client: TestClient) -> None:
    register(client, "alice@example.com")
    _add(client, "1--first")
    html = _library(client)

    card = html.split('data-slug-url="1--first"', 1)[1].split("</article>", 1)[0]
    assert 'data-role="library-more"' in card
    # PR 300: data-title-name heads the action sheet on phones.
    assert (
        'aria-label="Действия с тайтлом «Test Novel»"\n  data-title-name="Test Novel"\n  hidden'
    ) in card
    menu = card.split('data-role="library-menu" hidden>', 1)[1].split("</div>", 1)[0]
    assert re.findall(r'role="menuitem"[^>]*>(?:<svg.*?</svg>)?([^<]+)<', menu, re.S) == [
        "Страница тайтла",
        "Оглавление",
        "Удалить из библиотеки",
    ]
    assert 'href="/titles/1--first#title-panel-toc"' in menu
    confirm = card.split('data-role="library-confirm" hidden>', 1)[1]
    assert "Удалить из библиотеки?" in confirm
    assert "«Test Novel» пропадёт из библиотеки." in confirm
    assert "EPUB-файлы, которые вы уже скачали, останутся на устройстве." in confirm
    assert ">Отмена</button>" in confirm and ">Удалить</button>" in confirm


async def test_hero_menu_has_its_toc_item_for_phones_only(client: TestClient) -> None:
    register(client, "alice@example.com")
    _add(client, "1--first")
    async with connection() as conn:
        await record_progress(conn, 1, "1--first", "1", "3")

    hero = _library(client).split('data-role="library-hero"', 1)[1].split("</section>", 1)[0]
    menu = hero.split('data-role="library-menu" hidden>', 1)[1].split("</div>", 1)[0]
    assert "Страница тайтла" in menu
    # Desktop has the «Оглавление» button beside «Продолжить»; phones drop it for the
    # action sheet (PR 300), so the item is there but phone-only.
    assert 'class="wn-library-menu__item wn-library-menu__item--phone-only"' in menu
    assert "Оглавление" in menu
    assert "Удалить из библиотеки" in menu
    assert (
        'data-slug-url="1--first"' in _library(client).split('data-role="library-hero"', 1)[1][:80]
    )


async def test_chapter_zero_is_the_prologue(client: TestClient) -> None:
    register(client, "alice@example.com")
    _add(client, "1--first")
    async with connection() as conn:
        await record_progress(conn, 1, "1--first", "2", "0")

    with patch("app.api.library.reading_progress_percent", return_value=10):
        html = _library(client)

    assert "Том 2 · Пролог" in html
    assert "Продолжить · Пролог" in html
    assert "Остановились на прологе" in html


def test_card_actions_script_is_wired_and_portals_its_popovers() -> None:
    script = (ROOT / "app/static/js/library-card-actions.js").read_text(encoding="utf-8")
    assert "document.body.append(menu, confirm);" in script
    assert 'headers: { Accept: "application/json" }' in script
    assert 'new CustomEvent("library:changed")' in script


def test_removal_is_undoable_for_six_seconds() -> None:
    script = (ROOT / "app/static/js/library-card-actions.js").read_text(encoding="utf-8")
    assert "const UNDO_MS = 6000;" in script
    assert 'showToast("Удалено из библиотеки", { label: "Вернуть", run: undo });' in script
    # Nothing reaches the server until the toast's time is up (or the page is left).
    assert "timer: setTimeout(commit, UNDO_MS)" in script
    assert 'window.addEventListener("pagehide"' in script
    assert "send(p, { keepalive: true });" in script
    assert 'showToast("Не удалось удалить из библиотеки");' in script
    toolbar = (ROOT / "app/static/js/library-toolbar.js").read_text(encoding="utf-8")
    assert 'document.addEventListener("library:changed"' in toolbar


# --- catalog: «В библиотеку» --------------------------------------------------------


def _toggle(html: str, slug: str) -> str:
    match = re.search(
        rf'<button\s+type="button"\s+class="[^"]+"\s+data-role="catalog-library-toggle"\s+'
        rf'data-slug-url="{re.escape(slug)}"[^>]*>',
        html,
    )
    assert match is not None, slug
    return match.group(0)


def test_catalog_cards_know_whats_in_the_library(client: TestClient) -> None:
    register(client, "alice@example.com")
    _add(client, "2--test-novel-2")
    fake = _FakeCatalog(
        CatalogPage(items=_titles(1, 3), page=1, has_next_page=True),
        featured=[CatalogPage(items=[], page=1, has_next_page=False)],
    )
    with patch("app.services.catalog.Catalog", return_value=fake):
        html = client.get("/catalog").text
        fragment = client.get("/catalog/page", params={"page": 2}).text

    on = _toggle(html, "2--test-novel-2")
    assert 'aria-pressed="true"' in on
    assert 'aria-label="«Novel 2» в библиотеке — убрать"' in on
    off = _toggle(html, "1--test-novel-1")
    assert 'aria-pressed="false"' in off
    assert 'aria-label="Добавить «Novel 1» в библиотеку"' in off
    # The appended pages know it too.
    assert 'aria-pressed="true"' in _toggle(fragment, "2--test-novel-2")
    # The switch counts the same set.
    assert 'data-role="library-switch-count">1</span>' in html


def test_featured_toggle_reads_in_library(client: TestClient) -> None:
    register(client, "alice@example.com")
    _add(client, "1001--test-novel-1001")
    fake = _FakeCatalog(
        CatalogPage(items=_titles(1, 12), page=1, has_next_page=False),
        featured=[CatalogPage(items=_titles(1001, 1), page=1, has_next_page=False)],
    )
    with patch("app.services.catalog.Catalog", return_value=fake):
        html = client.get("/catalog").text

    featured = html.split('data-role="catalog-featured"', 1)[1].split("</article>", 1)[0]
    toggle = _toggle(featured, "1001--test-novel-1001")
    assert 'class="catalog-featured__lib"' in toggle
    assert 'aria-pressed="true"' in toggle
    assert '<span data-role="catalog-library-label">В библиотеке</span>' in featured


def test_toggle_script_flips_at_once_and_never_asks() -> None:
    script = (ROOT / "app/static/js/catalog-library-toggle.js").read_text(encoding="utf-8")
    assert '`/library/${encodeURIComponent(slug)}/${wasOn ? "remove" : "add"}`' in script
    assert "confirm(" not in script  # nothing to lose for a catalog title
    assert 'window.location.assign("/login");' in script  # a session that ended
    assert 'showToast("Не удалось обновить библиотеку");' in script


# --- no star anywhere: «В библиотеку» / «Удалить из библиотеки» only -----------------

STAR_PATH = "M12 2l3 7 7 .3"  # the old favorite star's icon (PR 123, removed in PR 293)


async def test_library_actions_are_never_mistaken_for_the_old_star(client: TestClient) -> None:
    register(client, "alice@example.com")
    _add(client, "1--test-novel-1", "2--test-novel-2")
    async with connection() as conn:
        await record_progress(conn, 1, "1--test-novel-1", "1", "3")
    fake = _FakeCatalog(
        CatalogPage(items=_titles(1, 14), page=1, has_next_page=False),
        featured=[CatalogPage(items=_titles(1001, 2), page=1, has_next_page=False)],
    )

    pages = {"/library": _library(client)}
    with patch("app.services.catalog.Catalog", return_value=fake):
        pages["/catalog"] = client.get("/catalog").text
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        pages["/profile"] = client.get("/profile").text
    pages["/settings/account"] = client.get("/settings/account").text

    for path, html in pages.items():
        assert STAR_PATH not in html, path
        assert "избранн" not in html.lower(), path
        assert "favorite" not in html.lower(), path

    # What the library and the catalog do say instead.
    assert "Удалить из библиотеки" in pages["/library"]
    toggles = re.findall(
        r'data-role="catalog-library-toggle"[^>]*aria-label="([^"]+)"', pages["/catalog"]
    )
    assert toggles and all("библиотек" in label for label in toggles)
    assert '<span data-role="catalog-library-label">В библиотеку</span>' in pages["/catalog"]

"""PR 297: the desktop library screen (LibraryDesktop.dc.html / Catalog handoff.md)."""

import re
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
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

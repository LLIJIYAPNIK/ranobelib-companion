"""Issue 394: title suggestions under «Найти тайтл по названию» - the suggest service,
GET /catalog/suggest and the form/script wiring. The SDK is faked at the services edge."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib import CatalogPage, RateLimitError
from ranobelib.catalog import MIN_PER_PAGE
from ranobelib.models import Country, Cover, Label, Title

from app.config import get_settings
from app.services.catalog import SUGGEST_MAX, SUGGEST_MIN_QUERY, suggest_titles
from tests.db_reset import reset_app_database

ROOT = Path(__file__).resolve().parents[1]
ONGOING = Label(id=1, label="Онгоинг")
KOREA = Country(id=11, name="Корея")


def _title(
    i: int,
    *,
    name: str = "Solo Leveling",
    rus_name: str | None = "Поднятие уровня в одиночку",
    eng_name: str | None = "Solo Leveling",
    cover: Cover | None = None,
    status: Label = ONGOING,
    country: Country | None = KOREA,
) -> Title:
    return Title(
        id=i,
        name=name,
        rus_name=rus_name,
        eng_name=eng_name,
        slug=f"title-{i}",
        slug_url=f"{i}--title-{i}",
        cover=cover or Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=status,
        country=country,
    )


class _FakeCatalog:
    def __init__(self, items: list[Title] | None = None, error: Exception | None = None) -> None:
        self._items = items or []
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> "_FakeCatalog":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def list_titles(self, **kwargs: Any) -> CatalogPage:
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return CatalogPage(items=self._items, page=1, has_next_page=False)


# --- The service ---------------------------------------------------------------------


@pytest.mark.parametrize("query", ["", " ", "a", " a "])
async def test_short_query_never_reaches_the_sdk(query: str) -> None:
    catalog = _FakeCatalog([_title(1)])
    with patch("app.services.catalog.get_catalog", return_value=catalog):
        assert await suggest_titles(query) == []
    assert catalog.calls == []
    assert SUGGEST_MIN_QUERY == 2


async def test_suggestions_are_the_catalogs_own_search_for_the_query() -> None:
    catalog = _FakeCatalog([_title(i) for i in range(10)])
    with patch("app.services.catalog.get_catalog", return_value=catalog):
        result = await suggest_titles("  поднятие ", 3)

    assert [t.id for t in result] == [0, 1, 2]
    # The same call the catalog makes for this query on Enter (its default sort, page 1),
    # at the smallest page the API accepts - no refresh, so the SDK's cache answers repeats.
    assert catalog.calls == [{"page": 1, "per_page": MIN_PER_PAGE, "query": "поднятие"}]


@pytest.mark.parametrize(("limit", "expected"), [(0, 1), (-5, 1), (8, 8), (50, SUGGEST_MAX)])
async def test_limit_is_clamped_to_one_page(limit: int, expected: int) -> None:
    catalog = _FakeCatalog([_title(i) for i in range(MIN_PER_PAGE)])
    with patch("app.services.catalog.get_catalog", return_value=catalog):
        result = await suggest_titles("поднятие", limit)

    assert len(result) == expected


# --- The route -----------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


def test_suggest_returns_what_a_catalog_card_shows(client: TestClient) -> None:
    covered = _title(
        1,
        cover=Cover(
            thumbnail="https://cover.imglib.info/1_thumb.jpg",
            default="https://cover.imglib.info/1.jpg",
        ),
    )
    bare = _title(2, rus_name=None, name="Only Name", eng_name=None, country=None)
    catalog = _FakeCatalog([covered, bare])
    with patch("app.services.catalog.get_catalog", return_value=catalog):
        response = client.get("/catalog/suggest", params={"query": " solo "})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, max-age=60"
    body = response.json()
    assert body["query"] == "solo"
    assert body["all_url"] == "/catalog?query=solo"
    assert body["items"] == [
        {
            "slug_url": "1--title-1",
            "url": "/titles/1--title-1",
            "name": "Поднятие уровня в одиночку",
            # eng_name and name are the same here - listed once.
            "alt_names": ["Solo Leveling"],
            "cover": "https://cover.imglib.info/1_thumb.jpg",
            "meta": "Онгоинг · Корея",
        },
        {
            "slug_url": "2--title-2",
            "url": "/titles/2--title-2",
            "name": "Only Name",
            "alt_names": [],
            "cover": None,
            "meta": "Онгоинг",
        },
    ]


def test_suggest_for_a_short_query_is_empty_without_a_request(client: TestClient) -> None:
    catalog = _FakeCatalog([_title(1)])
    with patch("app.services.catalog.get_catalog", return_value=catalog):
        response = client.get("/catalog/suggest", params={"query": "s"})

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert catalog.calls == []


@pytest.mark.parametrize("limit", [0, 11, "many"])
def test_suggest_rejects_a_limit_outside_one_page(client: TestClient, limit: object) -> None:
    response = client.get("/catalog/suggest", params={"query": "solo", "limit": limit})
    assert response.status_code == 422


def test_suggest_passes_the_limit_through(client: TestClient) -> None:
    catalog = _FakeCatalog([_title(i) for i in range(10)])
    with patch("app.services.catalog.get_catalog", return_value=catalog):
        response = client.get("/catalog/suggest", params={"query": "solo", "limit": 3})

    assert len(response.json()["items"]) == 3


def test_sdk_errors_go_through_the_central_mapping_as_json(client: TestClient) -> None:
    catalog = _FakeCatalog(error=RateLimitError())
    with patch("app.services.catalog.get_catalog", return_value=catalog):
        response = client.get(
            "/catalog/suggest",
            params={"query": "solo"},
            headers={"Accept": "application/json"},
        )

    assert response.status_code == 429
    assert "detail" in response.json()


# --- The wiring ----------------------------------------------------------------------


def test_every_search_form_is_wired_to_the_suggestions() -> None:
    macros = (ROOT / "app/templates/home/_macros.html").read_text(encoding="utf-8")
    assert 'aria-label="Поиск по каталогу" data-role="search-suggest"' in macros
    base = (ROOT / "app/templates/base.html").read_text(encoding="utf-8")
    assert "{{ static_url('js/search-suggest.js') }}\" defer" in base


def test_script_is_a_combobox_over_the_suggest_endpoint() -> None:
    script = (ROOT / "app/static/js/search-suggest.js").read_text(encoding="utf-8")
    for fragment in (
        "`/catalog/suggest?",
        "AbortController",
        "DEBOUNCE_MS = 250",
        "MIN_QUERY = 2",
        'setAttribute("role", "combobox")',
        '"aria-expanded"',
        '"aria-activedescendant"',
        'setAttribute("role", "listbox")',
        'setAttribute("role", "option")',
        "Все результаты в каталоге →",
        "Ничего не нашлось",
        "Ищем…",
        '"Escape"',
        '"ArrowDown"',
        '"ArrowUp"',
    ):
        assert fragment in script, fragment
    # Names from the catalog are only ever text, never parsed as HTML.
    assert "innerHTML = item" not in script
    assert "item.name.replace" not in script

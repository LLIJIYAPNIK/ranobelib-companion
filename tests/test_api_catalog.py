from unittest.mock import patch

from fastapi.testclient import TestClient
from ranobelib import CatalogPage
from ranobelib.models import Cover, Label, Title

from app.main import app

client = TestClient(app)


def _fake_title(id_: int = 1, name: str = "Test Novel") -> Title:
    return Title(
        id=id_,
        name=name,
        slug=f"test-novel-{id_}",
        slug_url=f"{id_}--test-novel-{id_}",
        cover=Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
    )


class _FakeCatalog:
    def __init__(self, page: CatalogPage | None = None, exc: Exception | None = None) -> None:
        self._page = page
        self._exc = exc
        self.received_kwargs: dict[str, object] | None = None

    async def __aenter__(self) -> "_FakeCatalog":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def list_titles(self, **kwargs: object) -> CatalogPage:
        self.received_kwargs = kwargs
        if self._exc is not None:
            raise self._exc
        assert self._page is not None
        return self._page


def test_show_catalog_renders_cards() -> None:
    page = CatalogPage(items=[_fake_title(1, "High School DxD")], page=1, has_next_page=True)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog")

    assert response.status_code == 200
    assert "High School DxD" in response.text
    assert 'href="/titles/1--test-novel-1"' in response.text
    assert 'data-next-page="2"' in response.text


def test_show_catalog_no_next_page_leaves_data_next_page_empty() -> None:
    page = CatalogPage(items=[_fake_title()], page=1, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog")

    assert response.status_code == 200
    assert 'data-next-page=""' in response.text


def test_show_catalog_is_viewable_without_login() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog")

    assert response.status_code == 200  # no redirect to /login


def test_show_catalog_invalid_page_is_rejected() -> None:
    response = client.get("/library/catalog", params={"page": 0})

    assert response.status_code == 422


def test_show_catalog_passes_page_to_the_sdk() -> None:
    page = CatalogPage(items=[], page=3, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog", params={"page": 3})

    assert fake.received_kwargs["page"] == 3


def test_show_catalog_passes_query_to_the_sdk() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog", params={"query": "dxd"})

    assert fake.received_kwargs["query"] == "dxd"


def test_show_catalog_empty_query_is_treated_as_no_search() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog", params={"query": ""})

    assert fake.received_kwargs["query"] is None


def test_show_catalog_renders_query_in_search_input_and_data_attribute() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog", params={"query": "dxd"})

    assert 'value="dxd"' in response.text
    assert 'data-query="dxd"' in response.text


def test_catalog_page_fragment_passes_query_to_the_sdk() -> None:
    page = CatalogPage(items=[], page=2, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog/page", params={"query": "dxd", "page": 2})

    assert fake.received_kwargs["query"] == "dxd"


def test_catalog_page_fragment_returns_only_cards() -> None:
    page = CatalogPage(items=[_fake_title(1, "High School DxD")], page=2, has_next_page=True)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog/page", params={"page": 2})

    assert response.status_code == 200
    assert response.headers["X-Has-Next-Page"] == "true"
    assert "High School DxD" in response.text
    assert "<html" not in response.text
    assert "sidebar" not in response.text


def test_catalog_page_fragment_has_next_page_false() -> None:
    page = CatalogPage(items=[], page=5, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog/page", params={"page": 5})

    assert response.headers["X-Has-Next-Page"] == "false"


def test_library_tabs_active_state() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)

    library_response = client.get("/library")
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        catalog_response = client.get("/library/catalog")

    assert 'library-tabs__link library-tabs__link--active" href="/library"' in library_response.text
    assert (
        'library-tabs__link library-tabs__link--active" href="/library/catalog"'
        in catalog_response.text
    )

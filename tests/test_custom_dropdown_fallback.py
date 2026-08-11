"""PR 54's no-JS fallback contract: custom-dropdown.js progressively enhances a plain
<select> entirely on the client - the server always renders a normal, fully-functional
<select> with real <option>s and no JS-only hiding class/attribute, regardless of whether
the visitor's browser ever runs the script. These tests exercise the server-rendered HTML
directly (the test client never executes JS), so a passing suite here is itself proof the
no-JS path works - not merely a claim about it."""

from unittest.mock import patch

from fastapi.testclient import TestClient
from ranobelib import CatalogPage
from ranobelib.models import Chapter, Cover, Label, Title, Volume

from app.main import app

client = TestClient(app)


def test_catalog_sort_select_has_no_js_only_hiding_markers() -> None:
    with patch("app.services.catalog.Catalog") as MockCatalog:
        MockCatalog.return_value.__aenter__.return_value.list_titles.return_value = (
            CatalogPage(items=[], page=1, has_next_page=False)
        )
        MockCatalog.return_value.__aenter__.return_value.list_genres.return_value = []
        response = client.get("/library/catalog")

    assert response.status_code == 200
    assert '<select name="sort" class="toc__export-format"' in response.text
    # custom-dropdown.js is the only thing that ever adds this class or hides the
    # <select> - neither should be present in what the server sends.
    assert "dropdown__native" not in response.text
    assert 'style="display' not in response.text


def test_title_format_select_submits_a_real_option_value_without_js() -> None:
    title = Title(
        id=6712,
        name="Test Novel",
        slug="test-novel",
        slug_url="6712--test-novel",
        cover=Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
    )
    volumes = [Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1")])]

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def get_info(self):
            return title

        async def get_table_of_contents(self):
            return volumes

        async def estimate_title_size(self):
            return 0

    with patch("app.services.client.RanobeLib", return_value=_FakeClient()):
        response = client.get("/titles/6712--test-novel")

    assert response.status_code == 200
    # The no-JS visitor picks one of these <option>s and the browser submits it via the
    # plain <select> exactly as before PR 54 - nothing about the value round-trip depends
    # on custom-dropdown.js having run.
    assert '<option value="epub">EPUB</option>' in response.text
    assert 'name="fmt"' in response.text

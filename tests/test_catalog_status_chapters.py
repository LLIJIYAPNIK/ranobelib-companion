"""PR 303: the catalog's «Статус» and «Количество глав» filters (CatalogDesktop's
filters popover, CatalogMobile's filters sheet) - `Catalog.list_titles(statuses=[...],
min_chapters=...)` and `Catalog.list_statuses()`, SDK >=0.12.0."""

import re
from unittest.mock import patch

from ranobelib import CatalogPage
from ranobelib.models import Label

from app.services.catalog import list_catalog_titles, list_statuses
from tests.test_api_catalog import _FakeCatalog, _no_redirect_client, _titles, client
from tests.test_services_catalog import _FakeCatalog as _FakeListsCatalog
from tests.test_services_catalog import _GenreListingCatalog

STATUSES = [Label(id=1, label="Онгоинг"), Label(id=2, label="Завершён")]


def _empty() -> CatalogPage:
    return CatalogPage(items=[], page=1, has_next_page=False)


def _get(params: dict[str, object]) -> tuple[_FakeCatalog, str]:
    fake = _FakeCatalog(_empty(), statuses=STATUSES)
    with patch("app.services.catalog.Catalog", return_value=fake):
        response = client.get("/catalog", params=params)
    assert response.status_code == 200
    return fake, response.text


def _desktop_section(html: str, key: str) -> str:
    return html.split(f'data-section-key="{key}"', 1)[1].split("</section>", 1)[0]


def _sheet(html: str) -> str:
    return html.split('data-role="catalog-filters-sheet"', 1)[1].split("</form>", 1)[0]


# --- SDK calls --------------------------------------------------------------------------


def test_statuses_and_min_chapters_go_to_the_sdk() -> None:
    fake, _ = _get({"statuses": [1, 2], "min_chapters": 500})

    assert fake.received_kwargs["statuses"] == [1, 2]
    assert fake.received_kwargs["min_chapters"] == 500


def test_no_status_and_any_chapter_count_pass_none() -> None:
    fake, _ = _get({"min_chapters": 0})

    assert fake.received_kwargs["statuses"] is None
    assert fake.received_kwargs["min_chapters"] is None


def test_negative_min_chapters_is_rejected() -> None:
    response = client.get("/catalog", params={"min_chapters": -1})

    assert response.status_code == 422


def test_either_filter_switches_to_the_results_mode() -> None:
    for params in ({"statuses": 1}, {"min_chapters": 100}):
        fake, html = _get(params)
        assert 'data-mode="results"' in html
        assert fake.featured_calls == []


def test_any_chapter_count_keeps_the_editorial_mode() -> None:
    _, html = _get({"min_chapters": 0})

    assert 'data-mode="editorial"' in html


def test_infinite_scroll_page_forwards_both_filters() -> None:
    fake = _FakeCatalog(_empty(), statuses=STATUSES)
    with patch("app.services.catalog.Catalog", return_value=fake):
        response = client.get(
            "/catalog/page", params={"page": 2, "statuses": [2], "min_chapters": 1000}
        )

    assert response.status_code == 200
    assert fake.received_kwargs["statuses"] == [2]
    assert fake.received_kwargs["min_chapters"] == 1000
    assert fake.featured_calls == []


def test_random_forwards_both_filters_and_keeps_them_when_nothing_matches() -> None:
    fake = _FakeCatalog(_empty(), statuses=STATUSES)
    with patch("app.services.catalog.Catalog", return_value=fake):
        response = _no_redirect_client.get(
            "/catalog/random", params={"statuses": [1, 2], "min_chapters": 100}
        )

    assert fake.received_kwargs["statuses"] == [1, 2]
    assert fake.received_kwargs["min_chapters"] == 100
    assert response.status_code == 303
    location = response.headers["location"]
    assert "statuses=1&statuses=2" in location
    assert "min_chapters=100" in location
    assert "random_empty=1" in location


def test_sort_random_on_the_catalog_keeps_both_filters_in_the_redirect() -> None:
    response = _no_redirect_client.get(
        "/catalog", params={"sort": "random", "statuses": 2, "min_chapters": 500}
    )

    assert response.status_code == 303
    assert "statuses=2" in response.headers["location"]
    assert "min_chapters=500" in response.headers["location"]


# --- The page -----------------------------------------------------------------------------


def test_grid_carries_both_filters_for_infinite_scroll() -> None:
    _, html = _get({"statuses": [1, 2], "min_chapters": 100})

    assert 'data-statuses="1,2"' in html
    assert 'data-min-chapters="100"' in html


def test_grid_filter_attributes_are_empty_without_filters() -> None:
    _, html = _get({})

    assert 'data-statuses=""' in html
    assert 'data-min-chapters=""' in html


def test_criteria_chips_name_each_status_and_the_threshold() -> None:
    _, html = _get({"statuses": [1, 2], "min_chapters": 100})

    chips = html.split('data-role="catalog-criteria"', 1)[1].split("</ul>", 1)[0]
    assert 'aria-label="Убрать: Онгоинг"' in chips
    assert 'aria-label="Убрать: Завершён"' in chips
    assert 'aria-label="Убрать: от 100 глав"' in chips
    # Dropping «Онгоинг» keeps «Завершён» and the threshold.
    ongoing = re.search(r'href="([^"]*)" aria-label="Убрать: Онгоинг"', chips)
    assert ongoing is not None
    assert ongoing.group(1) == "/catalog?statuses=2&amp;min_chapters=100"
    threshold = re.search(r'href="([^"]*)" aria-label="Убрать: от 100 глав"', chips)
    assert threshold is not None
    assert threshold.group(1) == "/catalog?statuses=1&amp;statuses=2"


def test_filters_badge_counts_statuses_and_the_threshold() -> None:
    _, html = _get({"statuses": [1, 2], "min_chapters": 100, "genres": 5})

    assert 'aria-label="выбрано: 4"' in html


def test_desktop_status_section_lists_list_statuses_with_the_picked_ones_checked() -> None:
    _, html = _get({"statuses": 2})

    section = _desktop_section(html, "statuses")
    assert ">Статус (1)</button>" in section
    assert re.search(r'name="statuses"\s+value="1"\s+form="catalog-search-form"\s+>', section)
    assert re.search(
        r'name="statuses"\s+value="2"\s+form="catalog-search-form"\s+checked', section
    )
    assert "<span>Онгоинг</span>" in section
    assert "<span>Завершён</span>" in section


def test_desktop_status_section_is_left_out_without_statuses() -> None:
    fake = _FakeCatalog(_empty())
    with patch("app.services.catalog.Catalog", return_value=fake):
        html = client.get("/catalog").text

    assert 'data-section-key="statuses"' not in html


def test_desktop_chapter_section_is_one_radio_group_with_any_by_default() -> None:
    _, html = _get({})

    section = _desktop_section(html, "min-chapters")
    assert ">Количество глав</button>" in section
    values = re.findall(r'type="radio"\s+name="min_chapters"\s+value="(\d+)"', section)
    assert values == ["0", "100", "500", "1000"]
    assert re.search(r'value="0"\s+form="catalog-search-form"\s+checked', section)
    assert "<span>Любое</span>" in section
    assert "<span>от 500</span>" in section


def test_desktop_chapter_section_checks_the_current_threshold() -> None:
    _, html = _get({"min_chapters": 500})

    section = _desktop_section(html, "min-chapters")
    assert ">Количество глав (от 500)</button>" in section
    assert re.search(r'value="500"\s+form="catalog-search-form"\s+checked', section)
    assert not re.search(r'value="0"\s+form="catalog-search-form"\s+checked', section)


def test_sheet_groups_follow_the_design_order() -> None:
    _, html = _get({})

    legends = re.findall(r'<legend class="catalog-sheet__legend">([^<]+)</legend>', _sheet(html))
    assert legends[:3] == ["Сортировка", "Статус", "Количество глав"]


def test_sheet_status_and_chapter_options_reflect_the_page() -> None:
    _, html = _get({"statuses": 1, "min_chapters": 1000})

    sheet = _sheet(html)
    assert '<input type="checkbox" name="statuses" value="1" checked>' in sheet
    assert '<input type="checkbox" name="statuses" value="2">' in sheet
    assert '<input type="radio" name="min_chapters" value="1000" checked>' in sheet
    assert '<input type="radio" name="min_chapters" value="0">' in sheet


def test_empty_results_name_the_filters() -> None:
    _, html = _get({"min_chapters": 1000})

    assert "По выбранным фильтрам тайтлов нет." in html


def test_noscript_next_page_link_keeps_both_filters() -> None:
    fake = _FakeCatalog(
        CatalogPage(items=_titles(1, 30), page=1, has_next_page=True), statuses=STATUSES
    )
    with patch("app.services.catalog.Catalog", return_value=fake):
        html = client.get("/catalog", params={"statuses": 1, "min_chapters": 100}).text

    noscript = html.split("<noscript>", 1)[1].split("</noscript>", 1)[0]
    assert "&statuses=1&min_chapters=100&page=2" in noscript


def test_desktop_radio_is_styled_round() -> None:
    from pathlib import Path

    css = (Path(__file__).resolve().parent.parent / "app/static/css/app.css").read_text(
        encoding="utf-8"
    )
    rule = css.split('.catalog-filters__option input[type="radio"] {', 1)[1].split("}", 1)[0]
    assert "appearance: none;" in rule
    assert "border-radius: 50%;" in rule


# --- Service ------------------------------------------------------------------------------


async def test_list_statuses_delegates_to_the_catalog_client() -> None:
    fake = _FakeListsCatalog(statuses=STATUSES)
    with patch("app.services.catalog.get_catalog", return_value=fake):
        assert await list_statuses() == STATUSES


async def test_several_genres_forward_both_filters_to_every_genre_listing() -> None:
    catalog = _GenreListingCatalog({5: [1, 2], 8: [3]})

    await list_catalog_titles(
        catalog,  # type: ignore[arg-type]
        page=1,
        query=None,
        sort="last_chapter_at",
        genres=[5, 8],
        countries=[],
        tags=[],
        statuses=[1],
        min_chapters=100,
    )

    assert catalog.calls
    for call in catalog.calls:
        assert call["statuses"] == [1]
        assert call["min_chapters"] == 100

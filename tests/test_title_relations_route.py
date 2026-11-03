"""PR 361: «Связанные релизы» on the title page - GET /titles/{slug}/relations renders the
fragment title-relations.js fetches into the slot at the end of the reading column; 204
when there is nothing to show, so the page simply goes without the section."""

from unittest.mock import patch

from fastapi.testclient import TestClient
from ranobelib import RateLimitError, Relation

from app.main import app
from tests.test_titles import _fake_title, _FakeClient

client = TestClient(app, follow_redirects=False)


def _relation(
    label: str,
    *,
    site: int,
    model: str = "manga",
    slug_url: str,
    name: str,
    type_label: str | None = None,
    cover: str | None = None,
) -> Relation:
    return Relation.model_validate(
        {
            "related_type": {"id": 1, "label": label},
            "media": {
                "id": 1,
                "name": name,
                "slug": slug_url.split("--", 1)[-1],
                "slug_url": slug_url,
                "model": model,
                "site": site,
                "cover": {"default": cover} if cover else {},
                "type": {"id": 1, "label": type_label} if type_label else None,
                "status": {"id": 2, "label": "Завершён"},
            },
        }
    )


SEQUEL = _relation(
    "Продолжение",
    site=3,
    slug_url="22513--shin",
    name="Истинная D×D",
    type_label="Япония",
    cover="https://cover.imglib.info/s.jpg",
)
MANGA = _relation("Адаптация", site=2, slug_url="620--dxd", name="DxD манга", type_label="Манга")
TV = _relation(
    "Адаптация", site=5, model="anime", slug_url="10--tv", name="DxD", type_label="TV Сериал"
)
OVA = _relation(
    "Адаптация", site=5, model="anime", slug_url="11--ova", name="DxD OVA", type_label="OVA"
)
UNKNOWN = _relation("Адаптация", site=42, slug_url="1--x", name="Где-то ещё")


class _RelationsClient(_FakeClient):
    def __init__(self, relations: list[Relation] | Exception) -> None:
        super().__init__(_fake_title())
        self._relations = relations

    async def get_relations(self) -> list[Relation]:
        if isinstance(self._relations, Exception):
            raise self._relations
        return self._relations


def _get(relations: list[Relation] | Exception):
    with patch("app.services.client.RanobeLib", return_value=_RelationsClient(relations)):
        return client.get("/titles/6712--test-novel/relations")


def test_no_relations_is_204_and_no_markup() -> None:
    response = _get([])

    assert response.status_code == 204
    assert response.text == ""


def test_relations_that_cant_be_fetched_are_204_not_an_error() -> None:
    assert _get(RateLimitError()).status_code == 204


def test_groups_become_tabs_with_their_counts_in_the_api_order() -> None:
    response = _get([TV, SEQUEL, OVA, MANGA])

    assert response.status_code == 200
    html = response.text
    assert 'data-role="relations-tabs"' in html
    assert (
        html.index('data-group="novels"')
        < html.index('data-group="manga"')
        < html.index('data-group="anime"')
    )
    assert 'Аниме <span class="hm-num">2</span>' in html
    assert html.index("TV Сериал") < html.index("OVA")


def test_a_novel_opens_inside_the_app_with_how_it_relates() -> None:
    html = _get([SEQUEL]).text

    assert 'href="/titles/22513--shin"' in html
    assert 'href="/titles/22513--shin" target="_blank"' not in html
    assert "Продолжение" in html
    assert "Япония" not in html  # a novel's type is its country - not what the card says
    assert 'src="https://cover.imglib.info/s.jpg"' in html


def test_manga_and_anime_leave_for_their_site_and_say_so() -> None:
    html = _get([MANGA, TV]).text

    assert (
        'href="https://slashlib.me/ru/manga/620--dxd" target="_blank" rel="noopener noreferrer"'
        in html
    )
    assert 'href="https://anilib.me/ru/anime/10--tv" target="_blank"' in html
    assert "anilib.me · Завершён" in html
    assert "откроется в новой вкладке" in html
    assert "TV Сериал" in html and "Манга" in html


def test_one_group_shows_its_name_and_count_without_tabs() -> None:
    html = _get([TV, OVA]).text

    assert 'data-role="relations-tabs"' not in html
    assert 'class="tp-rel__sum">Аниме <span class="hm-num">2</span>' in html
    assert 'data-role="relations-label"' not in html


def test_a_site_without_a_known_page_is_a_card_without_a_link() -> None:
    html = _get([UNKNOWN]).text

    assert '<div class="tp-rel__card">' in html
    assert "Где-то ещё" in html
    assert "откроется в новой вкладке" not in html


def test_the_title_content_carries_the_slot_and_the_page_loads_its_script() -> None:
    with patch("app.services.client.RanobeLib", return_value=_RelationsClient([SEQUEL])):
        data = client.get("/titles/6712--test-novel/data")
        page = client.get("/titles/6712--test-novel")

    assert 'data-role="title-relations-slot" data-slug-url="6712--test-novel"' in data.text
    assert "Истинная D×D" not in data.text  # fetched separately, never in the first render
    assert "static/js/title-relations.js" in page.text

"""«Лампа»: how the title fragment (GET /titles/{slug}/data) shows its contents - the
shelf of volumes, the reading line with its folds, «Переводили», the missing cover."""

from unittest.mock import patch

from fastapi.testclient import TestClient
from ranobelib.models import Volume

from app.main import app
from tests.test_title_contents import _volume
from tests.test_titles import _fake_title, _FakeClient

client = TestClient(app)


def _page(volumes: list[Volume]) -> str:
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title(), volumes)):
        response = client.get("/titles/6712--test-novel/data")
    assert response.status_code == 200
    return response.text


def test_the_fragment_puts_every_volume_on_the_shelf_as_a_tab_of_its_chapters() -> None:
    html = _page([_volume("1", 3), _volume("2", 6, start=4)])
    assert html.count('data-role="shelf-spine"') == 2
    assert 'href="#vol-2" role="tab" id="spine-2" aria-controls="vol-2"' in html
    # As tall as its chapters: the longest volume is the tallest spine.
    assert "--h: 168px" in html and "--h: 116px" in html
    assert 'id="vol-1" role="tabpanel" aria-labelledby="spine-1"' in html
    assert 'formaction="/titles/6712--test-novel/volumes/2/export"' in html


def test_a_long_volume_folds_its_tail_behind_a_head_that_opens_it() -> None:
    html = _page([_volume("1", 40)])
    assert 'data-role="toc-fold"' in html
    assert "Главы 13–40" in html
    assert "28 глав · показать" in html
    # The folded rows are there (search and «Весь том» reach them) but out of reach until opened.
    assert '<ol class="tp-line" inert>' in html
    assert 'value="1--40"' in html


def test_a_single_volume_title_omits_the_volume_shelf_and_renders_chapters_directly() -> None:
    html = _page([_volume("1", 5)])
    assert 'data-role="title-volumes"' not in html
    assert 'data-role="shelf-spine"' not in html
    assert 'id="vol-1"' in html
    assert 'role="tabpanel"' not in html
    assert 'aria-labelledby="spine-1"' not in html


def test_a_title_with_one_translation_per_chapter_says_who_translated_what() -> None:
    html = _page([_volume("1", 3, team="DUB1401"), _volume("2", 5, start=4, team="RKT TEAM")])
    assert "Главы переводили разные команды:" in html
    assert "У каждой главы один перевод, выбирать не из чего." not in html
    assert '<span>RKT TEAM</span><small class="hm-num">5 глав</small>' in html
    assert "Перевод по умолчанию" not in html


def test_more_than_three_translators_expand_in_a_details_disclosure() -> None:
    html = _page(
        [
            _volume("1", 5, start=1, team="Team A"),
            _volume("2", 4, start=6, team="Team B"),
            _volume("3", 3, start=10, team="Team C"),
            _volume("4", 2, start=13, team="Team D"),
            _volume("5", 1, start=15, team="Team E"),
        ]
    )
    assert '<details class="tp-teams__more">' in html
    assert (
        '<summary class="tp-teams__toggle">'
        '<span>Ещё 2</span><small class="hm-num">3 главы</small></summary>' in html
    )
    assert '<span>Team D</span><small class="hm-num">2 главы</small>' in html
    assert '<span>Team E</span><small class="hm-num">1 глава</small>' in html


def test_a_title_without_a_cover_shows_its_name_in_the_cover_place() -> None:
    title = _fake_title().model_copy(
        update={
            "cover": _fake_title().cover.model_copy(
                update={"default": None, "md": None, "thumbnail": None}
            )
        }
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, [_volume("1", 2)])):
        html = client.get("/titles/6712--test-novel/data").text
    assert 'class="tp-cover tp-cover--none" role="img" aria-label="Обложки нет"' in html
    assert 'class="hm-ambient"' not in html

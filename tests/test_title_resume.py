"""PR 372: a title already being read puts the way back in first - «Вы остановились» (the
reader's chapter and the next two) at the top of the reading column, «Связанные релизы»
folded into one line."""

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi import Request
from fastapi.testclient import TestClient
from ranobelib.models import Chapter, Volume

from app.auth.dependencies import get_current_user
from app.db.library import LibraryEntry
from app.db.users import User
from app.main import app
from tests.test_title_relations_route import MANGA, SEQUEL, TV, _get
from tests.test_titles import _fake_title, _FakeClient

ROOT = Path(__file__).parents[1]
client = TestClient(app, follow_redirects=False)

VOLUMES = [
    Volume(
        number="1",
        chapters=[
            Chapter(id=1, volume="1", number="1", name="Начало"),
            Chapter(id=2, volume="1", number="2", name="Середина"),
        ],
    ),
    Volume(
        number="2",
        chapters=[
            Chapter(id=3, volume="2", number="3", name="Новый том"),
            Chapter(id=4, volume="2", number="4", name="Дальше"),
        ],
    ),
]
READER = User(id=1, email="r@example.com", password_hash="", created_at="2026-01-01", nickname="r")


@asynccontextmanager
async def _no_db():
    yield None


def _data(place: tuple[str, str] | None) -> str:
    """The title's /data fragment for a reader whose library entry is at `place` (None:
    not in the library)."""

    async def get_entry(conn, user_id, slug_url):
        if place is None:
            return None
        return LibraryEntry(
            id=1, user_id=1, slug_url=slug_url, added_at="2026-01-01",
            last_read_volume=place[0], last_read_number=place[1], last_read_at="2026-11-01",
            default_translation_index=None, last_read_paragraph=None,
            last_read_paragraph_total=None,
        )  # fmt: skip

    def as_reader(request: Request) -> User:
        request.state.current_user = READER
        return READER

    app.dependency_overrides[get_current_user] = as_reader
    try:
        with (
            patch(
                "app.services.client.RanobeLib", return_value=_FakeClient(_fake_title(), VOLUMES)
            ),
            patch("app.api.titles.connection", _no_db),
            patch("app.api.titles.get_entry", get_entry),
        ):
            response = client.get("/titles/6712--test-novel/data")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    return response.text


def _resume(html: str) -> str:
    start = html.index('<section class="tp-resume')
    return html[start : html.index("</section>", start)]


def test_the_readers_chapter_and_the_next_two_come_first() -> None:
    html = _data(("1", "2"))
    resume = _resume(html)

    assert "Вы остановились" in resume
    assert 'href="/titles/6712--test-novel/chapters/1/2" aria-current="step"' in resume
    assert "Продолжить" in resume
    # The next two run on into the next volume, which says so.
    assert 'href="/titles/6712--test-novel/chapters/2/3"' in resume
    assert "Том 2 · Глава 3" in resume
    assert 'href="/titles/6712--test-novel/chapters/2/4"' in resume
    assert "Начало" not in resume
    # Before the description and the chapters; no data-role, so the full list's search
    # and picker never see these rows twice.
    assert html.index('class="tp-resume') < html.index('class="tp-about')
    assert 'data-role="toc-chapter"' not in resume


def test_the_last_chapter_stands_alone() -> None:
    resume = _resume(_data(("2", "4")))

    assert 'href="/titles/6712--test-novel/chapters/2/4" aria-current="step"' in resume
    assert resume.count('<li class="tp-row') == 1


def test_a_title_not_started_has_no_resume_and_the_releases_whole() -> None:
    html = _data(None)

    assert "tp-resume" not in html
    assert 'data-role="title-relations-slot" data-slug-url="6712--test-novel">' in html


def test_a_title_being_read_folds_the_releases() -> None:
    html = _data(("1", "1"))

    assert 'data-slug-url="6712--test-novel" data-compact="true"' in html


def test_the_releases_fold_into_one_line_with_their_groups() -> None:
    html = _get([TV, MANGA, SEQUEL]).text
    peek = html[html.index('data-role="relations-peek"') : html.index("</button>")]

    assert 'aria-controls="tp-rel-body"' in peek
    assert 'Аниме <span class="hm-num">1</span> · Манга <span class="hm-num">1</span>' in peek
    assert peek.count("<img") == 1  # only the novel has a cover here
    assert 'id="tp-rel-body" data-role="relations-body"' in html


def test_the_resume_is_desktop_only_and_has_its_place_in_the_grid() -> None:
    css = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")

    assert '"head lamp" "resume lamp" "about lamp" "rel lamp"' in css
    assert "@media (max-width: 1023px) {\n  .tp-resume {\n    display: none;" in css

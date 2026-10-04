from pathlib import Path
from types import SimpleNamespace

from app.templating import templates

ROOT = Path(__file__).parents[1]


def _hero(variant: str) -> str:
    entry = SimpleNamespace(
        slug_url="6712--test-novel",
        last_read_volume="2",
        last_read_number="17",
    )
    item = {
        "entry": entry,
        "name": "Test Novel",
        "cover_url": "https://example.test/cover.jpg",
        "progress_percent": 41,
        "last_read_label": "Читали сегодня",
    }
    macro = templates.env.get_template("_continue_reading_hero.html").module
    return str(
        macro.continue_reading_hero(
            item,
            variant=variant,
            title_tag="h1" if variant == "home" else "h2",
            title_id=f"{variant}-continue-title",
        )
    )


def test_home_and_library_share_continue_toc_and_progress_contract() -> None:
    home = _hero("home")
    library = _hero("library")

    for html in (home, library):
        assert 'href="/titles/6712--test-novel/chapters/2/17"' in html
        assert 'href="/titles/6712--test-novel#title-panel-toc"' in html
        assert 'aria-valuenow="41"' in html
        assert 'style="width: 41%"' in html
        assert "Продолжить · Гл. 17" in html


def test_routes_invoke_the_shared_macro_instead_of_copying_hero_markup() -> None:
    home = (ROOT / "app/templates/index.html").read_text(encoding="utf-8")
    library = (ROOT / "app/templates/library.html").read_text(encoding="utf-8")

    assert 'continue_reading_hero(dashboard.hero, variant="home"' in home
    assert 'continue_reading_hero(continue_item, title_id="library-continue-title")' in library
    assert "wn-library-hero__card" not in home
    assert "wn-library-hero__card" not in library

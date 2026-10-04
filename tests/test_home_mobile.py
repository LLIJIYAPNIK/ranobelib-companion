"""PR 280 (Webnovells Mobile, wave 34): the home screen on phones."""

import re
from pathlib import Path

CSS = (Path(__file__).parents[1] / "app/static/css/app.css").read_text(encoding="utf-8")


def _rule(selector: str, *, indent: str = "") -> str:
    """The selector's last rule at this indent - for an indented one, the <= 767px block
    (the home's mobile rules come after its tablet ones)."""
    matches = re.findall(rf"\n{indent}{re.escape(selector)} \{{([^}}]*)\}}", CSS)
    assert matches, selector
    return matches[-1]


def test_shared_hero_progress_bar_is_not_collapsed() -> None:
    progress = _rule(
        ".wn-home .wn-continue-hero--home .wn-library-hero__progress", indent="  "
    )
    assert "grid-column: 1 / -1;" in progress
    assert "align-items: center;" in progress
    assert "height: 6px;" in _rule(
        ".wn-home .wn-continue-hero--home .wn-library-hero__bar", indent="  "
    )


def test_today_stats_carry_the_tile_and_the_row_wording() -> None:
    template = (Path(__file__).parents[1] / "app/templates/index.html").read_text(encoding="utf-8")

    for tile, row in (
        ("глав<br>прочитано", "Глав прочитано"),
        ("активного<br>чтения", "Активное чтение"),
        ("скачано<br>сегодня", "Скачано"),
    ):
        assert f'<span class="wn-home-today__tile-label">{tile}</span>' in template
        assert f'<span class="wn-home-today__row-label">{row}</span>' in template


def test_today_shows_one_wording_per_layout() -> None:
    assert "display: none;" in _rule(".wn-home-today__stats .wn-home-today__row-label")
    assert "display: none;" in _rule(
        ".wn-home-today__stats .wn-home-today__tile-label", indent="  "
    )
    mobile_row = _rule(".wn-home-today__stats .wn-home-today__row-label", indent="  ")
    assert "display: block;" in mobile_row


def test_mobile_home_rows_wrap_instead_of_squeezing() -> None:
    # The shared hero keeps the PR 280 behaviour: actions share a row while they fit,
    # then wrap without squeezing at 320/375px.
    assert "flex-wrap: wrap;" in _rule(
        ".wn-home .wn-continue-hero--home .wn-library-hero__actions", indent="  "
    )
    assert "flex: 1 1 160px;" in _rule(
        ".wn-home .wn-continue-hero--home .wn-library-hero__continue", indent="  "
    )
    assert "flex: 1 1 120px;" in _rule(
        ".wn-home .wn-continue-hero--home .wn-library-hero__toc", indent="  "
    )
    assert "flex-wrap: wrap;" in _rule(".wn-home__open", indent="  ")
    assert "flex: 1 1 220px;" in _rule(".wn-home__open-field", indent="  ")


def test_hero_title_is_capped_to_its_column_so_long_words_break() -> None:
    # Found by the five-width check: an unbroken word sized the start-aligned heading
    # past the column, so overflow-wrap never had a narrower box to break in.
    title = _rule(
        ".wn-home .wn-continue-hero--home .wn-library-hero__title", indent="  "
    )
    assert "max-width: 100%;" in title
    assert "overflow-wrap: anywhere;" in title

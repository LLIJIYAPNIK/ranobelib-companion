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


def test_hero_progress_bar_is_not_collapsed() -> None:
    # Since PR 273 the hero's bar was 0px tall and «Прочитано»/«41%» huddled in the
    # middle: .ui-progress centers its items and gives the bar flex: 1, which in this
    # column layout means a 0 basis for its height.
    assert "align-items: stretch;" in _rule(".wn-home-progress")
    assert "flex: none;" in _rule(".wn-home-progress .ui-progress__bar")


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
    # Webnovells Mobile -> Главная: the hero's CTA and «Оглавление», and the link field
    # and «Открыть», share a row while both fit and stack otherwise - no breakpoint.
    cta = _rule(".wn-home-hero__actions .wn-btn", indent="  ")
    assert "flex: 1 1 220px;" in cta
    assert "white-space: normal;" in cta
    assert "flex-basis: 140px;" in _rule(".wn-home-hero__actions .wn-btn--secondary", indent="  ")
    assert "flex-wrap: wrap;" in _rule(".wn-home__open", indent="  ")
    assert "flex: 1 1 220px;" in _rule(".wn-home__open-field", indent="  ")
    assert "flex: 1 1 150px;" in _rule(".wn-home-hero__text", indent="  ")


def test_hero_title_is_capped_to_its_column_so_long_words_break() -> None:
    # Found by the five-width check: an unbroken word sized the start-aligned heading
    # past the column, so overflow-wrap never had a narrower box to break in.
    assert "max-width: 100%;" in _rule(".wn-home-hero__title", indent="  ")

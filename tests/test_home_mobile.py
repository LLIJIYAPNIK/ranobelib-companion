"""PR 280 (Webnovells Mobile, wave 34): the home screen on phones."""

import re
from pathlib import Path

CSS = (Path(__file__).parents[1] / "app/static/css/app.css").read_text(encoding="utf-8")


def _rule(selector: str, *, indent: str = "") -> str:
    match = re.search(rf"\n{indent}{re.escape(selector)} \{{([^}}]*)\}}", CSS)
    assert match, selector
    return match.group(1)


def test_hero_progress_bar_is_not_collapsed() -> None:
    # Since PR 273 the hero's bar was 0px tall and «Прочитано»/«41%» huddled in the
    # middle: .ui-progress centers its items and gives the bar flex: 1, which in this
    # column layout means a 0 basis for its height.
    assert "align-items: stretch;" in _rule(".wn-home-progress")
    assert "flex: none;" in _rule(".wn-home-progress .ui-progress__bar")

"""PR 362: «Связанные релизы» on phones (app.css, the .tp-rel block) - swiped, not stepped,
group tabs on their own row at a thumb's height, the strip inside the page's margins."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")


def _phone() -> str:
    start = CSS.index("@media (max-width: 767px) {\n  .tp-rel {")
    return CSS[start : CSS.index("\n}\n", start)]


def _rule(block: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)} \{{([^}}]*)\}}", block)
    assert match, selector
    return match.group(1)


def test_tabs_take_their_own_row_at_a_thumbs_height() -> None:
    phone = _phone()
    tabs = _rule(phone, ".tp-rel__tabs")
    assert "flex: 1 0 100%;" in tabs
    assert "margin-left: 0;" in tabs
    assert "height: 44px;" in _rule(phone, ".tp-rel__tab")


def test_no_arrows_on_a_phone_the_strip_is_swiped() -> None:
    assert "display: none;" in _rule(_phone(), ".tp-rel--overflow .tp-rel__nav")


def test_the_strip_keeps_the_page_margins_and_its_swipe() -> None:
    strip = _rule(_phone(), ".tp-rel__strip")
    assert "grid-auto-columns: 112px;" in strip
    # A sideways swipe at the strip's end stays in the strip - not a browser back gesture.
    assert "overscroll-behavior-x: contain;" in strip
    assert "margin: 12px -16px" not in strip  # no bleed past the margins: text off the edge


def test_the_section_has_a_place_in_the_phone_grid() -> None:
    assert re.search(r'"chap chap"\s+"rel rel"\s+"about about"', CSS)

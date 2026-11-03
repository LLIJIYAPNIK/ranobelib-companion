"""The shared bottom sheet (bottom-sheet.js) drawn as the redesigned pages: the dark card,
glass close, and one look for every kind of content handed to it."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    matches = re.findall(rf"\n{re.escape(selector)} \{{([^}}]*)\}}", CSS)
    assert matches, selector
    return matches[-1]


ROWS = (
    ".bottom-sheet :is(.wn-library-menu__item, .wn-friend-sheet__item, "
    ".ui-menu__item, .profile-menu__item)"
)
PILLS = (
    ".bottom-sheet :is(.wn-sheet-confirm__cancel, .wn-sheet-confirm__danger, "
    ".wn-library-confirm__cancel, .wn-library-confirm__remove)"
)


def test_the_frame_is_the_redesigned_dark_card() -> None:
    root = _rule(".bottom-sheet")
    assert "--sheet-bg: #0d0d13;" in root
    assert "--sheet-edge: rgb(255 255 255 / 9%);" in root
    panel = _rule(".bottom-sheet__panel")
    assert "background: var(--sheet-bg);" in panel
    assert "border: 1px solid var(--sheet-edge);" in panel
    assert "border-radius: 28px 28px 0 0;" in panel
    # No rule under the head; the close is a round glass button with a 44px hit area.
    assert "border-bottom" not in _rule(".bottom-sheet__head")
    close = _rule(".bottom-sheet__close")
    assert "border-radius: 50%;" in close
    assert "width: 44px;" in close and "height: 44px;" in close
    assert "background: var(--sheet-glass);" in close


def test_no_violet_left_in_the_sheet() -> None:
    for selector in (
        ".bottom-sheet__panel",
        ".bottom-sheet__grabber",
        ".catalog-sheet__reset",
        ".catalog-sheet__apply",
        ".profile-menu__panel--in-sheet .profile-menu__profile-link",
    ):
        rule = _rule(selector)
        for violet in ("#7a52f4", "#b7a6ff", "#c9bcff", "#2a2838", "#55506e", "#16161f"):
            assert violet not in rule, (selector, violet)


def test_menus_share_one_row() -> None:
    row = _rule(ROWS)
    assert "min-height: 52px;" in row
    assert "border-radius: 16px;" in row
    assert "padding: 0 14px;" in row
    # Out by the rows' padding, so their text keeps the title's 20px edge.
    for menu in (
        ".bottom-sheet .wn-library-menu",
        ".wn-friend-sheet__menu",
        ".bottom-sheet .wn-profile-more__menu",
        ".bottom-sheet .profile-menu__panel--in-sheet",
    ):
        assert "margin: 0 -14px;" in _rule(menu), menu
    danger = (
        ".bottom-sheet :is(.wn-library-menu__item--danger, "
        ".wn-friend-sheet__item--danger, .profile-menu__item--danger)"
    )
    assert danger + ",\n" in CSS


def test_confirmations_are_two_pills_the_removal_tinted_red() -> None:
    pills = _rule(PILLS)
    assert "min-height: 52px;" in pills
    assert "border-radius: 999px;" in pills
    assert "background: var(--sheet-glass);" in _rule(
        ".bottom-sheet :is(.wn-sheet-confirm__cancel, .wn-library-confirm__cancel)"
    )
    danger = _rule(".bottom-sheet :is(.wn-sheet-confirm__danger, .wn-library-confirm__remove)")
    assert "background: var(--sheet-danger-bg);" in danger
    assert "color: var(--sheet-danger);" in danger


def test_the_primary_is_the_light_pill() -> None:
    primary = _rule(".bottom-sheet .ui-btn--primary")
    assert "--ui-btn-bg: var(--sheet-light);" in primary
    assert "--ui-btn-fg: #0c0c12;" in primary
    apply = _rule(".catalog-sheet__apply")
    assert "background: var(--sheet-light, #eeecf4);" in apply
    assert "border-radius: 999px;" in apply


def test_filters_read_as_the_catalogs_chips() -> None:
    legend = _rule(".catalog-sheet__legend")
    assert "text-transform" not in legend
    assert "letter-spacing" not in legend
    chosen = _rule(".catalog-sheet__option:has(input:checked)")
    assert "background: #f4f2fa;" in chosen
    assert "color: #0c0c12;" in chosen
    # The foot moves with the body's 24px bottom padding.
    foot = _rule(".catalog-sheet__foot")
    assert "bottom: calc(-24px - env(safe-area-inset-bottom, 0px));" in foot
    assert "padding: 4px 20px calc(24px + env(safe-area-inset-bottom, 0px));" in _rule(
        ".bottom-sheet__body"
    )


def test_segmented_tracks_and_fields_take_the_sheet_glass() -> None:
    assert "border-radius: 999px;" in _rule(".bottom-sheet .ui-segmented")
    checked = _rule(".bottom-sheet .ui-segmented__item input:checked + span")
    assert "background: var(--sheet-light);" in checked
    # background-color, not the shorthand: the select keeps its own chevron.
    fields = _rule(".bottom-sheet :is(.ui-input, .ui-select)")
    assert "background-color: rgb(255 255 255 / 4%);" in fields
    assert "background:" not in fields
    comments = _rule(".bottom-sheet .paragraph-comments__layer")
    assert "--pc-line: var(--sheet-edge);" in comments

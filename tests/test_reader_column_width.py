"""PR 316: the reading column fills the .main flex column and long words wrap inside it."""

import re
from pathlib import Path

CSS = (Path(__file__).parents[1] / "app/static/css/app.css").read_text(encoding="utf-8")


def _rules(selector: str, *, indent: str = "") -> list[str]:
    matches = re.findall(rf"\n{indent}{re.escape(selector)} \{{([^}}]*)\}}", CSS)
    assert matches, selector
    return matches


def test_main_is_a_flex_column_so_the_reader_needs_an_explicit_width() -> None:
    main = _rules(".main")[0]
    assert "display: flex;" in main
    assert "flex-direction: column;" in main


def test_reader_is_stretched_and_centered_without_auto_margins() -> None:
    desktop = _rules(".reader")[0]
    assert "width: 100%;" in desktop
    assert "max-width: min(var(--reader-width), calc(100% - 64px));" in desktop
    assert "min-width: 0;" in desktop
    assert "align-self: center;" in desktop
    for rule in _rules(".reader") + _rules(".reader", indent="  "):
        assert "margin: 0 auto" not in rule
        assert "width: auto" not in rule


def test_phone_reader_fills_the_screen_with_padding_inside() -> None:
    phone = _rules(".reader", indent="  ")[0]
    assert "max-width: 100%;" in phone
    assert "22px" in phone
    assert "box-sizing: border-box;" in _rules("*")[0]


def test_long_words_and_urls_wrap_inside_the_column() -> None:
    content = _rules(".reader-content")[-1]
    assert "min-width: 0;" in content
    assert "overflow-wrap: anywhere;" in content
    assert "min-width: 0;" in _rules(".reader-content__paragraph-wrap")[-1]


def test_tempo_words_stay_inline_block_but_cannot_outgrow_the_line() -> None:
    word = _rules(".reader-content__word")[0]
    assert "display: inline-block;" in word
    assert "max-width: 100%;" in word

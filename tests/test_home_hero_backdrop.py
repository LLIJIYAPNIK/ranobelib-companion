"""PR 319: the «Продолжить чтение» hero's blurred backdrop is a layer, never a flow block.

The ≥768px rules make .wn-library-hero__backdrop/__scrim absolute layers; below 768px
only the per-screen blocks do (library: PR 300, home: PR 280's max-width: 1100px block).
Without position: absolute the home's backdrop on phones was an in-flow image nearly two
cover widths tall and pushed «Продолжить» under the bottom bar.
"""

import re
from pathlib import Path

CSS = (Path(__file__).parents[1] / "app/static/css/app.css").read_text(encoding="utf-8")


def _media_rules(selector: str) -> list[tuple[str, str]]:
    """(enclosing @media query, body) for every rule with exactly this selector."""
    out = []
    for match in re.finditer(rf"\n( *){re.escape(selector)} \{{([^}}]*)\}}", CSS):
        media = ""
        if match.group(1):
            media = re.findall(r"\n@media ([^{]+)\{", CSS[: match.start()])[-1].strip()
        out.append((media, match.group(2)))
    assert out, selector
    return out


def _single(selector: str, media: str) -> str:
    bodies = [body for query, body in _media_rules(selector) if query == media]
    assert len(bodies) == 1, (selector, media)
    return bodies[0]


LAYER = ("position: absolute;", "z-index:")


def test_backdrop_is_an_absolute_cover_layer_at_every_hero_breakpoint() -> None:
    for selector, media in (
        (".wn-library-hero__backdrop", "(min-width: 768px)"),
        (".wn-library .wn-library-hero__backdrop", "(max-width: 767px)"),
        (".wn-home .wn-continue-hero--home .wn-library-hero__backdrop", "(max-width: 1100px)"),
    ):
        body = _single(selector, media)
        for declaration in (*LAYER, "object-fit: cover;"):
            assert declaration in body, (selector, declaration)


def test_scrim_is_an_absolute_layer_at_every_hero_breakpoint() -> None:
    for selector, media in (
        (".wn-library-hero__scrim", "(min-width: 768px)"),
        (".wn-library .wn-library-hero__scrim", "(max-width: 767px)"),
        (".wn-home .wn-continue-hero--home .wn-library-hero__scrim", "(max-width: 1100px)"),
    ):
        body = _single(selector, media)
        for declaration in (*LAYER, "inset: 0;"):
            assert declaration in body, (selector, declaration)


def test_home_backdrop_sits_under_the_scrim() -> None:
    def z(selector: str) -> int:
        body = _single(selector, "(max-width: 1100px)")
        return int(re.search(r"z-index: (-?\d+);", body).group(1))

    prefix = ".wn-home .wn-continue-hero--home .wn-library-hero__"
    assert z(prefix + "backdrop") < z(prefix + "scrim") < 0


def test_card_is_the_positioned_isolated_container() -> None:
    card = _single(".wn-library-hero__card", "")
    assert "position: relative;" in card
    assert "overflow: hidden;" in card
    assert "isolation: isolate;" in card


def test_narrow_phone_hero_gap_is_a_real_property() -> None:
    inner = _single(
        ".wn-home .wn-continue-hero--home .wn-library-hero__inner", "(max-width: 359px)"
    )
    assert "column-gap: 14px;" in inner
    assert "gap-inline" not in CSS

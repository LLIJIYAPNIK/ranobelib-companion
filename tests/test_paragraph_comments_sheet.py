"""PR 318: the paragraph comment layer keeps its look inside the phone bottom sheet.

On phones paragraph-menu.js moves the layer out of .reader-content__paragraph-wrap into
[data-role="bottom-sheet-body"]. Its colors must come from --pc-* (re-pointed at the
sheet's Webnovells palette by `.bottom-sheet .paragraph-comments__layer`), never from a
reader-theme --r-<role> token directly - that would show the light/sepia reader colors
inside the dark sheet. Radii (--r-xs ... --r-full) are theme-independent and fine.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
MENU_JS = (ROOT / "app/static/js/paragraph-menu.js").read_text(encoding="utf-8")

LAYER_SELECTOR = re.compile(
    r"paragraph-comment|paragraph-reaction|paragraph-menu__panel|wn-reaction"
)
RADII = {"xs", "sm", "md", "lg", "xl", "2xl", "full"}


def _layer_rules() -> list[tuple[str, str]]:
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", CSS)
    return [(sel.strip(), body) for sel, body in rules if LAYER_SELECTOR.search(sel)]


def test_layer_rules_exist() -> None:
    assert len(_layer_rules()) > 50


def test_layer_colors_go_through_pc_tokens_only() -> None:
    offenders = []
    for selector, body in _layer_rules():
        if "--pc-text:" in body:
            continue  # the --pc-* definitions themselves
        for token in re.findall(r"var\(--r-([a-z0-9-]+)\)", body):
            if token not in RADII:
                offenders.append((selector.splitlines()[-1], token))
    assert offenders == []


def test_sheet_repoints_every_pc_token_the_layer_defines() -> None:
    inline = re.search(r"\.paragraph-comments__layer \{([^}]*--pc-text:[^}]*)\}", CSS).group(1)
    sheet = re.search(r"\n\.bottom-sheet \.paragraph-comments__layer \{([^}]*)\}", CSS).group(1)
    defined = set(re.findall(r"(--pc-[a-z-]+):", inline))
    assert defined
    assert defined <= set(re.findall(r"(--pc-[a-z-]+):", sheet))


def test_sheet_tokens_are_global() -> None:
    sheet = re.search(r"\n\.bottom-sheet \.paragraph-comments__layer \{([^}]*)\}", CSS).group(1)
    root = CSS[: CSS.index("[data-reader-theme=")]
    for token in re.findall(r"var\((--wn-[a-z-]+)\)", sheet):
        assert f"{token}:" in root, token


def test_emoji_palette_scrolls_into_view_when_opened() -> None:
    toggle = MENU_JS[MENU_JS.index('emojiToggle.addEventListener("click"') :]
    toggle = toggle[: toggle.index("});")]
    assert 'palette.scrollIntoView({ block: "nearest"' in toggle

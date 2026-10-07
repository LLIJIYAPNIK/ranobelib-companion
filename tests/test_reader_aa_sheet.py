"""PR 321: the reader's Aa panel opens in the shared bottom sheet on phones."""

import re
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from ranobelib.models import Chapter

from app.main import app
from tests.test_chapters import _FakeClient, _three_chapter_volumes

ROOT = Path(__file__).parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
HUD = (ROOT / "app/static/js/reader-hud.js").read_text(encoding="utf-8")
TAP = (ROOT / "app/static/js/tap-to-read.js").read_text(encoding="utf-8")

client = TestClient(app)


def _chapter_page() -> str:
    chapter = Chapter(id=2, volume="1", number="2", content="<p>x</p>")
    with patch(
        "app.services.client.RanobeLib",
        return_value=_FakeClient(chapter, volumes=_three_chapter_volumes()),
    ):
        return client.get("/titles/6712--test-novel/chapters/1/2").text


def test_aa_controls_sit_in_one_movable_wrapper_inside_the_dialog() -> None:
    html = _chapter_page()
    start = html.index('data-role="reader-aa-panel"')
    panel = html[start : html.index("</dialog>", start)]
    content = panel[panel.index('data-role="reader-aa-content"') :]

    # Everything the sheet needs travels with the wrapper; the dialog's own head
    # (title + ✕) stays behind - the sheet has its own.
    for needle in (
        'data-setting="theme"',
        'data-setting="readerMode"',
        'href="/settings/reading"',
        'data-role="reader-settings-reset"',
    ):
        assert needle in content, needle
    assert 'data-role="reader-aa-close"' not in content
    assert 'data-role="bottom-sheet"' in html


def test_phone_opens_aa_through_the_shared_sheet_desktop_through_the_dialog() -> None:
    assert 'window.matchMedia("(max-width: 767px)")' in HUD
    opening = HUD[HUD.index("window.bottomSheet.open({") :]
    opening = opening[: opening.index("});")]
    assert "content: aaContent" in opening
    assert "opener: aa" in opening
    assert 'aa.setAttribute("aria-expanded", "false")' in opening
    assert "aaPanel.showModal()" in HUD


def test_hud_stays_pinned_and_escape_leaves_it_while_the_sheet_is_open() -> None:
    pinned = HUD[HUD.index("function pinned()") :]
    assert "sheetOpen()" in pinned[: pinned.index("}")]
    assert '!document.querySelector("dialog[open]") && !sheetOpen()' in HUD


def test_aa_control_rules_do_not_depend_on_the_dialog() -> None:
    assert not re.search(r"\.reader-aa \.", CSS)
    assert ".reader-aa__content .ui-segmented__item span" in CSS
    assert ".bottom-sheet .reader-aa__body" in CSS


def test_open_layers_block_pull_to_refresh() -> None:
    rule = re.search(
        r"html\.bottom-sheet-lock,\nhtml\.bottom-sheet-lock body,\nhtml:has\(dialog\[open\]\),\n"
        r"html:has\(dialog\[open\]\) body \{([^}]*)\}",
        CSS,
    )
    assert rule and "overscroll-behavior-y: contain;" in rule.group(1)
    grabber = re.search(r"\.ui-dialog--sheet \.ui-dialog__grabber \{([^}]*)\}", CSS).group(1)
    assert "touch-action: none;" in grabber
    head = re.search(r"\n\.bottom-sheet__head \{([^}]*)\}", CSS).group(1)
    assert "touch-action: none;" in head


def test_open_sheet_backdrop_is_not_a_tap_focus_tap() -> None:
    layer = re.search(r"const OPEN_LAYER =\s*\"([^\"]*)\"", TAP).group(1)
    assert ".bottom-sheet:not([hidden])" in layer
    assert '.bottom-sheet")) return;' in HUD

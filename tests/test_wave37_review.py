"""PR 315: fixes from the manual review of wave 37 («Webnovells Redesign: доводка») -
each one found by the review's browser sweep (320-1440px, keyboard, axe contrast)."""

import re
from pathlib import Path

import pytest

from tests.test_downloads_desktop import _entry, _render

ROOT = Path(__file__).parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
NOTIFICATIONS = (ROOT / "app/templates/notifications.html").read_text(encoding="utf-8")
TOOLS = (ROOT / "app/static/js/downloads-history-tools.js").read_text(encoding="utf-8")


def _rule(selector: str, *, indent: str = "") -> str:
    matches = re.findall(rf"\n{indent}{re.escape(selector)} \{{([^}}]*)\}}", CSS)
    assert matches, selector
    return matches[-1]


def test_notifications_settings_is_an_icon_button_on_phones() -> None:
    # At 320px «Настройки» pushed the heading row past the screen.
    assert '<span class="wn-notices__settings-label">Настройки</span>' in NOTIFICATIONS
    button = _rule(".wn-notices__settings", indent="  ")
    assert "width: 44px;" in button
    assert "min-height: 44px;" in button
    label = _rule(".wn-notices__settings-label", indent="  ")
    assert "clip: rect(0 0 0 0);" in label  # still the link's accessible name


def test_library_search_button_keeps_its_focus_ring() -> None:
    # The glass style's `box-shadow: none` outranked .wn-btn:focus-visible.
    glass = re.findall(r"\n  \.wn-library \.wn-library__add \.wn-btn \{([^}]*)\}", CSS)
    assert any("box-shadow: none;" in rule for rule in glass)
    ring = _rule(".wn-library .wn-library__add .wn-btn:focus-visible", indent="  ")
    assert "box-shadow: var(--wn-focus-ring);" in ring


def test_history_file_name_meets_contrast() -> None:
    # #7d7990 on the row was 4.4:1.
    assert "color: var(--wn-text-muted);" in _rule(".wn-downloads-row__file")
    assert "#7d7990" not in _rule(".wn-downloads-row__file")


def test_comment_kicker_meets_contrast_in_sepia() -> None:
    # The bare accent was 4.04:1 on the sepia card.
    kicker = _rule(".paragraph-comments__kicker")
    assert "color: color-mix(in srgb, var(--pc-accent) 70%, var(--pc-text));" in kicker


@pytest.mark.parametrize(("errors", "red"), [(0, False), (1, True)])
def test_summary_error_count_is_red_only_when_nonzero(
    monkeypatch: pytest.MonkeyPatch, errors: int, red: bool
) -> None:
    history = [_entry(9)] + [_entry(i, "error", error="Глава не найдена") for i in range(errors)]

    html = _render(monkeypatch, history)

    stat = re.search(r'<div class="([^"]*)" data-role="summary-error-stat">', html)
    assert stat
    assert ("wn-downloads-summary__stat--error" in stat.group(1)) is red
    assert 'toggle("wn-downloads-summary__stat--error", count("error") > 0)' in TOOLS

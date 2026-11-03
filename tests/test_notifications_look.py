"""«Уведомления» and the bell panel in the redesigned look: the title row's live count,
neutral kind markers, no gendered «(а)», the light and glass pills."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
CARD = (ROOT / "app/templates/_notification_card.html").read_text(encoding="utf-8")
PAGE = (ROOT / "app/templates/notifications.html").read_text(encoding="utf-8")
PAGE_JS = (ROOT / "app/static/js/notifications-page.js").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    matches = re.findall(rf"\n{re.escape(selector)} \{{([^}}]*)\}}", CSS)
    assert matches, selector
    return matches[-1]


def test_the_title_row_count_follows_marking_read() -> None:
    assert 'data-role="notifications-page-count"' in PAGE
    assert 'window.addEventListener("notifications:unreadchange"' in PAGE_JS
    assert "count.hidden = n === 0;" in PAGE_JS


def test_the_card_words_the_action_without_a_guessed_gender() -> None:
    markup = re.sub(r"\{#.*?#\}", "", CARD, flags=re.S)  # what renders, not the notes
    assert "(а)" not in markup
    for words in (
        "реакция на ваш комментарий",
        "хочет добавить вас в друзья",
        "теперь у вас в друзьях",
    ):
        assert words in CARD


def test_kind_markers_are_neutral_not_violet() -> None:
    kind = _rule(".wn-notice__kind")
    assert "background: #2a2a34;" in kind
    assert "var(--wn-primary)" not in kind
    assert ".wn-notice--friend-request .wn-notice__kind" not in CSS


def test_panel_and_page_are_the_dark_card() -> None:
    panel = _rule(".notifications-panel")
    assert "background: #0d0d13;" in panel
    assert "border-radius: 24px;" in panel
    footer = _rule(".notifications-panel__footer")
    assert "border-radius: 999px;" in footer
    assert "#b7a6ff" not in footer
    group = _rule(".wn-notices-group__title")
    assert "text-transform" not in group
    assert "letter-spacing: -0.01em;" in group
    assert "grid-template-columns: minmax(0, 1fr) 360px;" in _rule(".nt-layout")


def test_empty_states_carry_no_icon_in_a_tinted_circle() -> None:
    assert "wn-notices-empty__icon" not in PAGE
    assert "wn-notices-empty__icon" not in (
        ROOT / "app/templates/_notification_panel_list.html"
    ).read_text(encoding="utf-8")
    assert "background: #eeecf4;" in _rule(".wn-notices-empty__cta")

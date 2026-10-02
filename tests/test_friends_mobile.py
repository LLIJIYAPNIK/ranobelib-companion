"""PR 285 (Webnovells Mobile, wave 34): the friends screen on phones."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
PAGE = (ROOT / "app/templates/friends.html").read_text(encoding="utf-8")


def _rule(selector: str, *, indent: str = "") -> str:
    """The selector's last rule at this indent - for an indented one, the <= 767px block."""
    matches = re.findall(rf"\n{indent}{re.escape(selector)} \{{([^}}]*)\}}", CSS)
    assert matches, selector
    return matches[-1]


def test_search_button_wraps_under_the_field() -> None:
    assert "flex-wrap: wrap;" in _rule(".wn-friends-search", indent="  ")
    assert "flex: 1 1 200px;" in _rule(".wn-friends-search__field", indent="  ")
    button = _rule(".wn-friends-search .wn-btn", indent="  ")
    assert "flex: 1 0 auto;" in button
    assert "min-width: 112px;" in button
    assert 'enterkeyhint="search"' in PAGE


def test_search_results_are_cards_with_44px_actions() -> None:
    result = _rule(".wn-friends-result", indent="  ")
    assert "flex-direction: column;" in result
    assert "align-items: stretch;" in result
    assert "overflow-wrap: anywhere;" in _rule(
        ".wn-friends-result .wn-friend-card__name", indent="  "
    )
    assert "min-height: 44px;" in _rule(".wn-friends-result__actions .ui-btn", indent="  ")


def test_friends_tabs_are_a_ribbon() -> None:
    assert '<nav class="wn-friends-tabs" aria-label="Друзья и заявки" data-ribbon>' in PAGE
    assert "js/ribbon.js" in PAGE

    ribbon = _rule(".wn-friends-tabs", indent="  ")
    assert "position: relative;" in ribbon  # offsetLeft of the active tab is measured against it
    assert "margin: 0 calc(-1 * var(--wn-space-2));" in ribbon
    link = _rule(".wn-friends-tabs__link", indent="  ")
    assert "min-height: 44px;" in link
    assert "border-radius: var(--wn-radius-md);" in link

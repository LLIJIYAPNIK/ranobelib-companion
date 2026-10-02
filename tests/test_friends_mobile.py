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


def test_friend_cards_stack_in_one_column_with_wrapping_names() -> None:
    assert "grid-template-columns: minmax(0, 1fr);" in _rule(
        ".wn-friends__main,\n  .wn-friends__side", indent="  "
    )
    names = _rule(".wn-friend-card__name,\n  .wn-friend-card__title", indent="  ")
    assert "white-space: normal;" in names
    assert "overflow-wrap: anywhere;" in names


def test_card_actions_are_wrapping_44px_buttons() -> None:
    assert "flex-wrap: wrap;" in _rule(".wn-friend-card__actions", indent="  ")
    assert "flex: 1 1 120px;" in _rule(
        ".wn-friend-card__actions > *,\n  .wn-friend-card__actions > :first-child", indent="  "
    )
    buttons = re.search(
        r"\n  :is\(\.wn-friend-card__actions, \.wn-friends-request__actions\)\n"
        r"    :is\([^)]*\) \{([^}]*)\}",
        CSS,
    )
    assert buttons and "min-height: 44px;" in buttons.group(1)
    assert "min-height: 44px;" in _rule(".wn-friends-invite__nick button", indent="  ")
    assert "flex-wrap: wrap;" in _rule(".wn-friends-invite__nick", indent="  ")
    # The 26px switch keeps a 44px hit area.
    assert "inset: -9px 0;" in _rule(".wn-switch::before", indent="  ")

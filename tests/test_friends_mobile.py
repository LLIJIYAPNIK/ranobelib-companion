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


def test_friend_actions_open_in_the_shared_sheet_on_mobile() -> None:
    assert 'data-friend-sheet="friend-actions-{{ friend.user.id }}"' in PAGE
    assert 'aria-haspopup="dialog"' in PAGE
    assert 'id="friend-actions-{{ friend.user.id }}" data-role="friend-actions-sheet"' in PAGE
    assert 'data-bottom-sheet-title="{{ friend.user.display_name }}" hidden' in PAGE
    assert ">Открыть профиль</a>" in PAGE
    assert ">Удалить из друзей</button>" in PAGE
    # Removing asks first, in the same sheet, and posts to the existing route.
    confirm = PAGE.split('data-role="friend-remove-confirm"')[1].split("</form>")[0]
    assert 'action="/friends/{{ friend.user.id }}/remove"' in confirm
    assert "data-bottom-sheet-close" in confirm
    assert "js/friends-actions-sheet.js" in PAGE

    # «Ещё» exists only on phones, where the inline «Удалить» goes away.
    assert "display: none;" in _rule(".wn-friend-card__more")
    more = _rule(".wn-friend-card__more", indent="  ")
    assert "display: flex;" in more
    assert "width: 44px;" in more and "height: 44px;" in more
    assert "display: none;" in _rule(
        ".wn-friend-card__actions .wn-friend-card__remove", indent="  "
    )
    assert CSS.index("\n.wn-friend-card__more {") < CSS.index("\n  .wn-friend-card__more {")


def test_friend_sheet_swaps_to_the_confirmation_and_resets_on_close() -> None:
    script = (ROOT / "app/static/js/friends-actions-sheet.js").read_text(encoding="utf-8")
    assert "window.bottomSheet.open({" in script
    assert "onClose:" in script
    assert '[data-role="friend-remove-ask"]' in script
    assert "menu.hidden = true;" in script
    assert "confirm.hidden = false;" in script
    assert "min-height: 52px;" in _rule(".wn-friend-sheet__item")


def test_empty_tabs_are_dashed_notes_on_mobile() -> None:
    empty = _rule(".wn-friends-empty", indent="  ")
    assert "border: 1.5px dashed #2e2b40;" in empty
    assert "background: transparent;" in empty
    assert "display: none;" in _rule(".wn-friends-empty__icon", indent="  ")
    assert "Новых заявок нет" in PAGE
    assert "Отправленных заявок нет" in PAGE


def test_sheet_confirmation_wraps_a_long_nickname() -> None:
    assert "overflow-wrap: anywhere;" in _rule(".wn-sheet-confirm__text")

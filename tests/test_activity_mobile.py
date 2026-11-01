"""«Активность» on narrower screens and phones (app.css, the .ac block)."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "app/templates/activity.html").read_text(encoding="utf-8")


def _block(media: str) -> str:
    start = CSS.index(f"@media ({media}) {{\n  .hm.ac {{")
    return CSS[start : CSS.index("\n}\n", start)]


def _rule(selector: str) -> str:
    matches = re.findall(rf"\n{re.escape(selector)} \{{([^}}]*)\}}", CSS)
    assert matches, selector
    return matches[-1]


def test_one_column_puts_friends_and_the_site_before_the_event_list() -> None:
    block = _block("max-width: 1100px")
    assert "display: contents;" in block
    order = {
        name: int(re.search(rf"\.{name} \{{[^}}]*order: (\d)", block).group(1))
        for name in ("ac-month", "ac-friends", "ac-popular", "ac-hours", "ac-recent")
    }
    assert sorted(order, key=order.get) == [
        "ac-month",
        "ac-friends",
        "ac-popular",
        "ac-hours",
        "ac-recent",
    ]


def test_event_lines_wrap_on_phones() -> None:
    phone = _block("max-width: 767px")
    assert re.search(r"\.ac-event__title,\s*\.ac-event__sub \{\s*white-space: normal;", phone)


def test_month_arrows_and_days_are_44px_targets_on_phones() -> None:
    phone = _block("max-width: 767px")
    arrow = re.search(r"\.ac-cal__arrow \{([^}]*)\}", phone).group(1)
    assert "width: 44px;" in arrow
    assert "height: 44px;" in arrow
    assert "height: 44px;" in re.search(r"\.ac-day__tile \{([^}]*)\}", phone).group(1)


def test_figures_keep_tabular_numerals() -> None:
    assert "font-variant-numeric: tabular-nums;" in _rule(".hm.ac .hm-num")


def test_a_day_read_is_marked_under_its_number() -> None:
    mark = _rule(".ac-day--read .ac-day__tile::after")
    assert "background: var(--ac-teal);" in mark
    assert "opacity: var(--ac-mark, 1);" in mark


def test_a_long_friends_list_scrolls_inside_the_panel() -> None:
    more = _rule(".ac-more__list")
    assert "max-height: 336px;" in more
    assert "overflow-y: auto;" in more


def test_every_day_is_a_link_without_script() -> None:
    assert 'href="/activity?day={{ iso }}" data-day="{{ iso }}"' in TEMPLATE
    assert 'href="/activity?month={{ cal.prev_month }}"' in TEMPLATE


def test_no_old_activity_styles_left() -> None:
    assert "wn-activity" not in CSS
    assert ".activity-page" not in CSS


SCRIPT = (ROOT / "app/static/js/activity-calendar.js").read_text(encoding="utf-8")


def test_months_switch_in_place() -> None:
    """The month arrows swap the month's parts in place (no full reload): the calendar
    card and «Когда вы читаете» are marked, the script fetches the month and swaps them,
    keeps the month in the address and fetches the arrows' months ahead."""
    assert 'data-role="activity-calendar" data-month-part="calendar"' in TEMPLATE
    assert 'data-role="reading-hours" data-month-part="hours"' in TEMPLATE
    assert "[data-month-part]" in SCRIPT
    assert "history.pushState" in SCRIPT
    assert "popstate" in SCRIPT
    assert "prefetchNeighbours" in SCRIPT
    # A failed fetch still gets the reader to the month.
    assert "window.location.href = url" in SCRIPT


def test_the_day_card_has_no_title_link() -> None:
    """«О тайтле» belongs to the library and the home page, not to the day's card - its
    covers are the links."""
    assert "О тайтле" not in TEMPLATE

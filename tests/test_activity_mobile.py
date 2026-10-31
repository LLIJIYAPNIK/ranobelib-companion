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
        for name in ("ac-month", "ac-friends", "ac-popular", "ac-recent")
    }
    assert order["ac-month"] < order["ac-friends"] < order["ac-popular"] < order["ac-recent"]


def test_event_lines_wrap_on_phones() -> None:
    phone = _block("max-width: 767px")
    assert re.search(r"\.ac-event__title,\s*\.ac-event__sub \{\s*white-space: normal;", phone)


def test_month_arrows_and_days_are_44px_targets_on_phones() -> None:
    phone = _block("max-width: 767px")
    arrow = re.search(r"\.ac-cal__arrow \{([^}]*)\}", phone).group(1)
    assert "width: 44px;" in arrow
    assert "height: 44px;" in arrow
    assert "calc((var(--ac-dot) - 44px) / 2)" in CSS  # the day's hit area under a finger


def test_figures_keep_tabular_numerals() -> None:
    assert "font-variant-numeric: tabular-nums;" in _rule(".hm.ac .hm-num")


def test_the_chain_runs_behind_the_circles() -> None:
    assert "z-index: 0;" in _rule(".ac-day--chain-before::before,\n.ac-day--chain-after::after")
    assert "z-index: 1;" in _rule(".ac-day__dot")


def test_every_day_is_a_link_without_script() -> None:
    assert 'href="/activity?day={{ iso }}" data-day="{{ iso }}"' in TEMPLATE
    assert 'href="/activity?month={{ cal.prev_month }}"' in TEMPLATE


def test_no_old_activity_styles_left() -> None:
    assert "wn-activity" not in CSS
    assert ".activity-page" not in CSS

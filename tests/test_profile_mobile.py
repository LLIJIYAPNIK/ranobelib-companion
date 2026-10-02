"""PR 284 (Webnovells Mobile, wave 34): the profile screen on phones."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")


def _rule(selector: str, *, indent: str = "") -> str:
    """The selector's last rule at this indent - for an indented one, the <= 767px block."""
    matches = re.findall(rf"\n{indent}{re.escape(selector)} \{{([^}}]*)\}}", CSS)
    assert matches, selector
    return matches[-1]


def test_profile_hero_wraps_cleanly_at_phone_widths() -> None:
    hero = _rule(".wn-profile-hero__content", indent="  ")
    assert "flex-direction: column;" in hero
    assert "flex-wrap: wrap;" in hero
    assert "overflow-wrap: break-word;" in _rule(".wn-profile-hero__meta", indent="  ")

    actions = _rule(
        ".wn-profile-hero__actions,\n  .wn-profile-hero__actions > *", indent="  "
    )
    assert "flex: 1 1 144px;" in actions
    assert "min-width: 0;" in actions


def test_profile_stats_are_wrapping_pills_not_fixed_columns() -> None:
    stats = _rule(".wn-profile-stats", indent="  ")
    assert "display: flex;" in stats
    assert "flex-wrap: wrap;" in stats
    assert "grid-template-columns" not in stats

    pill = _rule(".wn-profile-stats__item", indent="  ")
    assert "flex: 1 1 120px;" in pill
    assert "border-radius: var(--wn-radius-md);" in pill


def test_reading_heatmap_is_a_ribbon_aligned_to_the_current_week() -> None:
    page = (ROOT / "app/templates/profile.html").read_text(encoding="utf-8")
    assert 'class="reading-calendar-scroll wn-ribbon"' in page
    assert "data-ribbon-current-week" in page
    assert "js/ribbon.js" in page

    ribbon = _rule(".wn-profile .reading-calendar-scroll", indent="  ")
    assert "max-width: 100%;" in ribbon
    assert "overscroll-behavior-x: contain;" in ribbon


def test_heatmap_alignment_is_initial_only_and_keeps_month_context() -> None:
    script = (ROOT / "app/static/js/ribbon.js").read_text(encoding="utf-8")
    assert 'hasAttribute("data-ribbon-current-week")' in script
    assert "ribbon.scrollWidth - ribbon.clientWidth" in script
    assert "requestAnimationFrame" in script

    months = _rule(".wn-profile .reading-calendar-months", indent="  ")
    assert "display: grid;" in months

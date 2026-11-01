"""«Профиль» in the site's redesign (.hm.pf): the hero, the year, the library and friends
on phones and narrower screens."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
PAGE = (ROOT / "app/templates/profile.html").read_text(encoding="utf-8")


def _rule(selector: str, *, indent: str = "") -> str:
    """The selector's last rule at this indent - for an indented one, the <= 767px block."""
    matches = re.findall(rf"\n{indent}{re.escape(selector)} \{{([^}}]*)\}}", CSS)
    assert matches, selector
    return matches[-1]


def test_profile_is_drawn_in_the_home_pages_frame() -> None:
    assert '<main class="hm ac pf' in PAGE
    assert 'class="hm-ambient"' in PAGE
    assert 'class="hl-art pf-hero__art"' in PAGE
    assert 'class="hm-panel pf-now"' in PAGE


def test_edit_sits_beside_the_name_and_the_meta_has_no_comment_count() -> None:
    line = PAGE.split('<div class="pf-id__line">')[1].split("</div>")[0]
    assert '<h1 class="pf-name">' in line
    assert 'class="pf-edit"' in line
    meta = PAGE.split('<p class="pf-meta hm-num">')[1].split("</p>")[0]
    assert "Комментариев" not in meta


def test_library_and_friends_share_a_row_on_desktop() -> None:
    grid = _rule(".pf-grid")
    assert "grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr);" in grid
    assert PAGE.index('class="hm-block pf-library') < PAGE.index('class="hm-block pf-friends')


def test_hero_stacks_on_phones() -> None:
    hero_id = _rule(".pf-id", indent="  ")
    assert "grid-template-columns: minmax(0, 1fr);" in hero_id
    assert '"stats"' in hero_id

    stats = _rule(".pf-stats", indent="  ")
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in stats

    now = _rule(".pf-now", indent="  ")
    assert "align-items: stretch;" in now
    assert "width: 100%;" in _rule(".pf-now__go", indent="  ")


def test_reading_is_the_month_calendar_of_activity() -> None:
    # One macro for both screens - no second calendar to drift from «Активность».
    assert '{% from "_reading_month.html" import reading_month %}' in PAGE
    assert "reading_month(reading, weekdays, calendar_base" in PAGE
    assert "js/activity-calendar.js" in PAGE
    activity = (ROOT / "app/templates/activity.html").read_text(encoding="utf-8")
    assert 'reading_month(overview.reading, weekdays, "/activity"' in activity
    # The year heatmap is gone with its tooltip.
    assert "reading-calendar" not in PAGE
    assert ".reading-calendar" not in CSS
    assert not (ROOT / "app/static/js/reading-calendar-tooltip.js").exists()


def test_library_preview_keeps_whole_rows() -> None:
    assert "grid-template-columns: repeat(5, minmax(0, 1fr));" in _rule(".pf-library__grid")
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in _rule(
        ".pf-library__grid", indent="  "
    )
    assert "display: none;" in _rule(".pf-library__grid > li:nth-child(n + 10)", indent="  ")


def test_friend_names_wrap_instead_of_ellipsizing_on_phones() -> None:
    names = _rule(".pf-friend__name,\n  .pf-friend__sub", indent="  ")
    assert "white-space: normal;" in names
    assert "overflow-wrap: break-word;" in names
    assert "text-overflow: clip;" in names

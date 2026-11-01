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
    assert '<main class="hm pf' in PAGE
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


def test_reading_heatmap_is_a_ribbon_aligned_to_the_current_week() -> None:
    assert 'class="reading-calendar-scroll wn-ribbon"' in PAGE
    assert "data-ribbon-current-week" in PAGE
    assert "js/ribbon.js" in PAGE

    ribbon = _rule(".pf .reading-calendar-scroll", indent="  ")
    assert "max-width: 100%;" in ribbon
    assert "overscroll-behavior-x: contain;" in ribbon
    assert "display: grid;" in _rule(".pf .reading-calendar-months", indent="  ")


def test_heatmap_alignment_is_initial_only_and_keeps_month_context() -> None:
    script = (ROOT / "app/static/js/ribbon.js").read_text(encoding="utf-8")
    assert 'hasAttribute("data-ribbon-current-week")' in script
    assert 'matchMedia("(max-width: 767px)")' in script
    assert "ribbon.scrollWidth - ribbon.clientWidth" in script
    assert "requestAnimationFrame" in script
    assert "new WeakSet()" in script


def test_calendar_tooltip_is_outside_the_rising_sections() -> None:
    # A fixed tooltip inside a transformed (.hm-rise) block would be placed against it.
    year = PAGE.split('<section class="hm-block pf-year')[1].split("</section>")[0]
    assert 'data-role="reading-calendar-tooltip"' not in year
    assert 'data-role="reading-calendar-tooltip"' in PAGE


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

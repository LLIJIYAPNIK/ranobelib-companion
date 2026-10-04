import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TEMPLATES = ROOT / "app/templates"
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")


def test_jinja_has_no_rendered_decorative_right_arrows() -> None:
    offenders: list[str] = []
    for path in TEMPLATES.glob("*.html"):
        source = path.read_text(encoding="utf-8")
        rendered_source = re.sub(r"\{#.*?#\}", "", source, flags=re.S)
        if "→" in rendered_source:
            offenders.append(path.name)

    assert offenders == []


def test_css_does_not_generate_a_textual_right_arrow() -> None:
    assert not re.search(r"content\s*:\s*[^;]*→", CSS)


def test_arrowless_links_keep_hover_and_focus_feedback() -> None:
    assert ".wn-home-section-head a:hover" in CSS
    assert ".wn-home-section-head a:focus-visible" in CSS
    assert ".wn-library-end__link:hover" in CSS
    assert ".wn-library-end__link:focus-visible" in CSS


def test_real_navigation_controls_remain_intact() -> None:
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    settings = (TEMPLATES / "settings_layout.html").read_text(encoding="utf-8")
    profile_friends = (TEMPLATES / "profile_friends.html").read_text(encoding="utf-8")
    library_switch = (ROOT / "app/static/js/library-switch.js").read_text(encoding="utf-8")

    assert 'class="sidebar__strip-back"' in base
    assert '<path d="M15 5l-7 7 7 7"/>' in base
    assert 'class="settings-back"' in settings
    assert "← Назад к профилю" in profile_friends
    assert 'event.key === "ArrowRight"' in library_switch
    assert 'event.key === "ArrowLeft"' in library_switch

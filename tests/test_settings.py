import re

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _nav_link_is_active(html: str, href: str) -> bool:
    pattern = rf'class="settings-nav__link settings-nav__link--active"\s+href="{re.escape(href)}"'
    return re.search(pattern, html) is not None


def test_settings_redirects_to_the_reading_tab() -> None:
    response = client.get("/settings", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/settings/reading"


def test_settings_nav_lists_all_three_sections_and_highlights_the_current_one() -> None:
    response = client.get("/settings/reading")

    assert response.status_code == 200
    assert 'class="settings-nav__link settings-nav__link--active"' in response.text
    for label, href in (
        ("Чтение", "/settings/reading"),
        ("Аккаунт", "/settings/account"),
        ("Безопасность", "/settings/security"),
    ):
        assert f'href="{href}"' in response.text
        assert f">{label}</a>" in response.text
    # Only the current tab (Чтение) is marked active, not all three.
    assert response.text.count("settings-nav__link--active") == 1


def test_settings_page_renders_mobile_nav_scaffolding() -> None:
    # PR 104: settings-mobile-nav.js drives the mobile "list first, then a section with a
    # back link" flow off these hooks - the back link starts `hidden` so desktop and
    # no-JS mobile visitors never see it (see app.css's [hidden] override).
    response = client.get("/settings/reading")

    assert response.status_code == 200
    assert 'data-role="settings-layout"' in response.text
    assert 'data-role="settings-nav"' in response.text
    back_tag = re.search(r'<a[^>]*data-role="settings-back"[^>]*>', response.text)
    assert back_tag is not None
    assert "hidden" in back_tag.group(0)
    assert 'href="/settings"' in back_tag.group(0)
    assert "static/js/settings-mobile-nav.js" in response.text


def test_settings_account_page_shows_a_locked_screen_for_anonymous_visitors() -> None:
    # PR 90: the account form only makes sense for a logged-in visitor - see
    # tests/test_api_settings_account.py for the form itself.
    response = client.get("/settings/account")

    assert response.status_code == 200
    assert _nav_link_is_active(response.text, "/settings/account")
    assert 'class="locked-feature"' in response.text
    assert 'data-role="reader-settings"' not in response.text


def test_settings_security_page_shows_a_locked_screen_for_anonymous_visitors() -> None:
    # PR 91: the password-change form only makes sense for a logged-in visitor - see
    # tests/test_api_settings_security.py for the form itself.
    response = client.get("/settings/security")

    assert response.status_code == 200
    assert _nav_link_is_active(response.text, "/settings/security")
    assert 'class="locked-feature"' in response.text
    assert 'data-role="reader-settings"' not in response.text


def test_settings_page_renders_reader_settings_panel() -> None:
    response = client.get("/settings/reading")

    assert response.status_code == 200
    assert 'data-role="reader-settings"' in response.text
    assert 'data-setting="fontFamily"' in response.text
    assert 'data-setting="fontSize"' in response.text
    assert 'data-setting="lineHeight"' in response.text
    assert 'data-setting="width"' in response.text
    assert "static/js/reader-settings.js" in response.text
    # PR 54: the fontFamily/lineHeight <select>s are progressively enhanced into custom
    # listboxes - script order matters here, it must load after reader-settings.js so the
    # trigger's initial label reflects the value that script just set from localStorage.
    assert response.text.index("static/js/reader-settings.js") < response.text.index(
        "static/js/custom-dropdown.js"
    )


def test_settings_page_groups_fields_into_named_sections() -> None:
    response = client.get("/settings/reading")

    assert response.status_code == 200
    assert "Типографика" in response.text
    assert "Чтение с помощью тапа" in response.text
    # The typography section's own heading comes before its fields, and before the
    # (currently empty) tap-to-read section - grouping, not just extra headings anywhere.
    typography_index = response.text.index("Типографика")
    tap_reading_index = response.text.index("Чтение с помощью тапа")
    assert typography_index < response.text.index('data-setting="fontFamily"') < tap_reading_index


def test_settings_page_offers_a_tap_to_read_toggle() -> None:
    response = client.get("/settings/reading")

    assert response.status_code == 200
    assert (
        'type="checkbox" class="reader-settings__toggle" data-setting="tapToRead"'
        in response.text
    )
    # It belongs to the "Чтение с помощью тапа" section (PR 61), not floating anywhere.
    assert response.text.index("Чтение с помощью тапа") < response.text.index(
        'data-setting="tapToRead"'
    )


def test_settings_page_offers_a_paragraph_style_choice() -> None:
    response = client.get("/settings/reading")

    assert response.status_code == 200
    assert 'data-setting="paragraphStyle"' in response.text
    assert '<option value="chat">' in response.text
    assert '<option value="plain">' in response.text
    # Same section as the tapToRead toggle - it's meaningless without that mechanic on.
    assert response.text.index("Чтение с помощью тапа") < response.text.index(
        'data-setting="paragraphStyle"'
    )


def test_settings_page_offers_a_paragraph_animation_choice() -> None:
    response = client.get("/settings/reading")

    assert response.status_code == 200
    assert 'data-setting="paragraphAnimation"' in response.text
    for value in ("none", "slide-up", "slide-left", "fade", "typewriter", "blur-focus"):
        assert f'<option value="{value}">' in response.text
    # Same section as the other tap-to-read options.
    assert response.text.index("Чтение с помощью тапа") < response.text.index(
        'data-setting="paragraphAnimation"'
    )


def test_settings_page_offers_a_reveal_tempo_choice() -> None:
    response = client.get("/settings/reading")

    assert response.status_code == 200
    assert 'data-setting="revealTempo"' in response.text
    for value in (
        "instant",
        "word-by-word",
        "typewriter-speed",
        "line-by-line",
        "highlight-sweep",
        "word-dissolve",
    ):
        assert f'<option value="{value}">' in response.text
    # Same section as the other tap-to-read options.
    assert response.text.index("Чтение с помощью тапа") < response.text.index(
        'data-setting="revealTempo"'
    )


def test_sidebar_links_to_settings_page() -> None:
    response = client.get("/settings/reading")

    assert response.status_code == 200
    assert 'href="/settings"' in response.text
    assert "sidebar__link--active" in response.text


def test_settings_page_offers_a_monospace_font_choice() -> None:
    response = client.get("/settings/reading")

    assert response.status_code == 200
    assert '<option value="mono">' in response.text


def test_settings_page_font_size_is_a_slider_with_live_preview() -> None:
    response = client.get("/settings/reading")

    assert response.status_code == 200
    assert 'type="range" id="fontSize" data-setting="fontSize"' in response.text
    assert 'data-role="reader-settings-preview"' in response.text


def test_settings_page_reading_width_is_a_slider() -> None:
    response = client.get("/settings/reading")

    assert response.status_code == 200
    assert 'type="range" id="width" data-setting="width"' in response.text


def test_settings_page_offers_a_reading_speed_test() -> None:
    response = client.get("/settings/reading")

    assert response.status_code == 200
    assert 'data-role="reading-speed-test"' in response.text
    assert 'data-role="reading-speed-start"' in response.text
    assert 'data-role="reading-speed-status"' in response.text
    assert 'data-role="reading-speed-sample"' in response.text
    assert 'data-role="reading-speed-value"' in response.text
    assert "Скорость чтения" in response.text
    assert "static/js/reading-speed-test.js" in response.text
    # Nested inside "Чтение с помощью тапа" (PR 77), not its own top-level section.
    tap_reading_index = response.text.index("Чтение с помощью тапа")
    assert tap_reading_index < response.text.index("Скорость чтения")


def test_settings_page_offers_manual_reading_speed_entry() -> None:
    response = client.get("/settings/reading")

    assert response.status_code == 200
    assert 'data-role="reading-speed-manual-input"' in response.text
    assert 'type="number"' in response.text
    assert 'min="50"' in response.text
    assert 'max="1000"' in response.text
    # Same subsection as the test (PR 77), not floating anywhere else.
    speed_section_index = response.text.index("Скорость чтения")
    assert speed_section_index < response.text.index('data-role="reading-speed-manual-input"')


def test_reading_speed_test_runs_inside_a_modal() -> None:
    """PR 125: the sample/status/countdown moved out of the page's own flow into a
    dismissible modal, same pattern as .title-quickview-modal (PR 117)."""
    response = client.get("/settings/reading")

    assert response.status_code == 200
    assert 'class="reading-speed-modal" data-role="reading-speed-modal"' in response.text
    assert 'data-role="reading-speed-modal-close"' in response.text
    assert 'data-role="reading-speed-timer"' in response.text

    modal_index = response.text.index('data-role="reading-speed-modal"')
    assert modal_index < response.text.index('data-role="reading-speed-status"')
    assert modal_index < response.text.index('data-role="reading-speed-sample"')


def test_reading_speed_manual_field_lives_outside_the_modal() -> None:
    """The roadmap's explicit check that PR 78's manual entry isn't affected by wrapping
    the test itself in a modal (PR 125) - the manual field markup comes before the modal
    in the page, i.e. it's a sibling in .reading-speed-test, not nested inside it."""
    response = client.get("/settings/reading")

    assert response.status_code == 200
    manual_index = response.text.index('data-role="reading-speed-manual-input"')
    modal_index = response.text.index('data-role="reading-speed-modal"')
    assert manual_index < modal_index

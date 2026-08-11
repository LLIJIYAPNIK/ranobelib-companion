from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_settings_page_renders_reader_settings_panel() -> None:
    response = client.get("/settings")

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


def test_sidebar_links_to_settings_page() -> None:
    response = client.get("/settings")

    assert response.status_code == 200
    assert 'href="/settings"' in response.text
    assert 'sidebar__link--active' in response.text


def test_settings_page_offers_a_monospace_font_choice() -> None:
    response = client.get("/settings")

    assert response.status_code == 200
    assert '<option value="mono">' in response.text


def test_settings_page_font_size_is_a_slider_with_live_preview() -> None:
    response = client.get("/settings")

    assert response.status_code == 200
    assert 'type="range" id="fontSize" data-setting="fontSize"' in response.text
    assert 'data-role="reader-settings-preview"' in response.text


def test_settings_page_reading_width_is_a_slider() -> None:
    response = client.get("/settings")

    assert response.status_code == 200
    assert 'type="range" id="width" data-setting="width"' in response.text

"""PR 335: «Офлайн» in the settings - what's downloaded on this device and how much room
it takes. The page is rendered empty and filled on the device (offline-settings.js), so
these tests cover the route, the nav and the markup the script hooks into."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.static_assets import asset_hash

client = TestClient(app)
_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "js"


def _page() -> str:
    response = client.get("/settings/offline")
    assert response.status_code == 200
    return response.text


def test_guests_get_the_offline_section_and_it_is_active_in_the_nav() -> None:
    html = _page()

    assert re.search(
        r'class="settings-nav__link settings-nav__link--active"\s+href="/settings/offline"', html
    )
    assert 'settings-nav__label">Офлайн</span>' in html
    assert html.count("settings-nav__link--active") == 1
    assert '<h2 class="settings-content__title">Офлайн</h2>' in html


def test_offline_is_listed_after_reading_on_every_settings_page() -> None:
    html = client.get("/settings/reading").text

    nav = html[html.index('data-role="settings-nav"') :]
    assert nav.index('href="/settings/reading"') < nav.index('href="/settings/offline"')


def test_the_summary_starts_hidden_and_the_store_loads_before_the_script() -> None:
    html = _page()

    assert re.search(r'data-role="offline-settings-summary"[^>]*hidden', html)
    assert re.search(r'data-role="offline-settings-unsupported"[^>]*hidden', html)
    assert re.search(r'data-role="offline-summary-warning"[^>]*hidden', html)
    for script in ("js/offline-store.js", "js/offline-settings.js"):
        assert f"/static/{script}?v={asset_hash(script)}" in html
    assert html.index("js/offline-store.js") < html.index("js/offline-settings.js")
    # Auto-download stays under «Чтение» - the page points there.
    assert 'href="/settings/reading"' in html[html.index('data-role="offline-settings"') :]


def test_titles_card_has_the_row_template_and_the_delete_sheet() -> None:
    html = _page()

    assert re.search(r'data-role="offline-settings-titles"[^>]*hidden', html)
    row = html[html.index('data-role="offline-titles-row"') :]
    for role in ("offline-titles-size", "offline-titles-opened", "offline-titles-delete"):
        assert f'data-role="{role}"' in row
    assert 'data-bottom-sheet-title="Удалить тайтл с устройства?"' in html
    assert 'data-role="offline-titles-delete-confirm"' in html


def test_auto_cleanup_is_offered_off_by_default_with_a_one_off_cleanup() -> None:
    html = _page()

    assert re.search(r'data-role="offline-settings-clean"[^>]*hidden', html)
    values = re.findall(
        r'name="offline-clean-behind" value="(\d+)" data-offline-setting="cleanBehind"', html
    )
    assert values == ["0", "5", "10", "25"]
    assert 'data-role="offline-clean-now"' in html
    assert 'data-bottom-sheet-title="Удалить прочитанное?"' in html
    # The store keeps the same choices, 0 (off) for anything else.
    store = (_JS / "offline-store.js").read_text(encoding="utf-8")
    assert "const CLEAN_CHOICES = [0, 5, 10, 25];" in store


def test_the_limit_is_offered_off_by_default_and_reached_offers_a_cleanup() -> None:
    html = _page()

    assert re.search(r'data-role="offline-settings-limit"[^>]*hidden', html)
    values = re.findall(r'name="offline-limit" value="(\d+)" data-offline-setting="limitMb"', html)
    assert values == ["0", "100", "250", "500", "1000"]
    assert re.search(r'data-role="offline-limit-reached"[^>]*hidden', html)
    assert 'data-role="offline-limit-clean"' in html
    store = (_JS / "offline-store.js").read_text(encoding="utf-8")
    assert "const LIMIT_CHOICES = [0, 100, 250, 500, 1000];" in store


def test_downloads_page_points_at_the_offline_settings() -> None:
    html = client.get("/downloads").text

    saved = html[html.index('data-role="offline-saved"') :]
    assert 'href="/settings/offline"' in saved

"""PR 328: the service worker, its offline page and its update flow.

The worker's routing - what is cached and what never is - and the update prompt in
sw-register.js are run under Node (node:vm, fake caches/fetch/DOM - see
tests/js/service_worker_harness.mjs and tests/js/sw_register_harness.mjs). The harness
gets the script exactly as /service-worker.js serves it, generated config included.
Those tests are skipped where Node isn't installed.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import pwa
from app.main import app
from app.static_assets import STATIC_DIR, asset_hash

_ROOT = Path(__file__).resolve().parent.parent
_SW_HARNESS = _ROOT / "tests" / "js" / "service_worker_harness.mjs"
_REGISTER_HARNESS = _ROOT / "tests" / "js" / "sw_register_harness.mjs"
_REGISTER_SCRIPT = STATIC_DIR / "js" / "sw-register.js"

client = TestClient(app)

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


def _node(harness: Path, script: Path) -> dict[str, Any]:
    completed = subprocess.run(
        ["node", str(harness), str(script)],
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def worker(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    script = tmp_path_factory.mktemp("sw") / "service-worker.js"
    script.write_text(client.get("/service-worker.js").text, encoding="utf-8")
    return _node(_SW_HARNESS, script)


@pytest.fixture(scope="module")
def register() -> dict[str, Any]:
    return _node(_REGISTER_HARNESS, _REGISTER_SCRIPT)


# --- the route ---------------------------------------------------------------------


def test_the_worker_is_served_from_the_root_uncached() -> None:
    response = client.get("/service-worker.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["cache-control"] == "no-cache"
    assert response.text.startswith("self.SW_CONFIG = {")


def test_only_the_worker_may_fetch_the_webfont_hosts() -> None:
    worker_csp = client.get("/service-worker.js").headers["content-security-policy"]
    page_csp = client.get("/health").headers["content-security-policy"]

    assert "script-src 'self'" in worker_csp
    assert "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com" in worker_csp
    assert "connect-src" not in page_csp


def test_precache_is_every_stylesheet_and_script_at_its_versioned_url() -> None:
    precache = pwa.service_worker_config()["precache"]

    assert precache[0] == "/offline"
    files = sorted(
        path.relative_to(STATIC_DIR).as_posix()
        for path in [*STATIC_DIR.glob("css/*.css"), *STATIC_DIR.glob("js/*.js")]
    )
    assert precache[1:] == [f"/static/{path}?v={asset_hash(path)}" for path in files]
    assert "/static/js/sw-register.js?v=" + asset_hash("js/sw-register.js") in precache


def test_a_changed_static_file_is_a_new_worker_version(monkeypatch: pytest.MonkeyPatch) -> None:
    before = pwa.service_worker_config()["version"]
    monkeypatch.setattr(
        pwa,
        "asset_hash",
        lambda path: "changed000" if path == "css/app.css" else asset_hash(path),
    )

    after = pwa.service_worker_config()

    assert "/static/css/app.css?v=changed000" in after["precache"]
    assert after["version"] != before


def test_the_version_follows_the_offline_page_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    before = pwa.service_worker_config()["version"]
    template = tmp_path / "offline.html"
    template.write_bytes(pwa.OFFLINE_TEMPLATE.read_bytes() + b"<!-- edited -->")
    monkeypatch.setattr(pwa, "OFFLINE_TEMPLATE", template)

    assert pwa.service_worker_config()["version"] != before


# --- the offline page ----------------------------------------------------------------


def test_offline_page_stands_alone_without_any_user_data() -> None:
    response = client.get("/offline")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    html = response.text
    assert "Нет соединения" in html
    # No shell: its sidebar would freeze whoever was signed in when it was cached.
    assert 'data-role="sidebar"' not in html
    assert "<script" not in html
    assert re.search(r'<a class="ui-btn ui-btn--primary" href="">Повторить</a>', html)
    assert 'href="/downloads"' in html
    assert f"/static/css/app.css?v={asset_hash('css/app.css')}" in html


def test_every_page_registers_the_worker_and_fetches_fonts_with_cors() -> None:
    html = client.get("/login").text

    assert f"/static/js/sw-register.js?v={asset_hash('js/sw-register.js')}" in html
    assert re.search(r'<link rel="stylesheet" crossorigin href="https://fonts\.googleapis', html)


# --- routing: what is cached and what never is ---------------------------------------


@needs_node
def test_only_static_files_and_webfonts_are_ever_cached(worker: dict[str, Any]) -> None:
    routes = dict(worker["routes"])

    assert routes["GET /static/css/app.css?v=abc no-cors"] == "static"
    assert routes["GET /static/js/reader-hud.js?v=abc no-cors"] == "static"
    assert routes["GET /static/icons/icon-192.png?v=abc no-cors"] == "image"
    assert routes["GET /static/favicon/favicon-32x32.png no-cors"] == "image"
    assert routes["GET https://fonts.googleapis.com/css2?family=Manrope cors"] == "font"
    assert routes["GET https://fonts.gstatic.com/s/manrope/v1/a.woff2 cors"] == "font"
    cached = {"static", "image", "font"}
    for request, route in routes.items():
        if route in cached:
            assert "/static/" in request or "fonts.g" in request, request


@needs_node
@pytest.mark.parametrize(
    "request_line",
    [
        "POST /login navigate",
        "POST /reading-progress/tick cors",
        "POST /activity/heartbeat cors",
        "GET /notifications/unread-count cors",
        "GET /notifications/panel cors",
        "GET /activity cors",
        "GET /admin/tables cors",
        "GET /settings/reading cors",
        "GET /downloads/status cors",
        "GET /titles/x/data cors",
        "GET /avatars/7.png no-cors",
        "GET /comment-attachments/a.mp4 no-cors",
        "GET /images/download?url=x cors",
        "GET /manifest.webmanifest cors",
        "GET /service-worker.js same-origin",
        # Unversioned: always revalidated over HTTP (PR 317), so not pinned in a cache.
        "GET /static/js/reader-hud.js no-cors",
        # Hotlinked covers/chapter images: opaque cross-origin responses.
        "GET https://cover.cdnlibs.org/uploads/cover.jpg no-cors",
        "GET https://ranobelib.me/uploads/ranobe/1.png no-cors",
    ],
)
def test_private_and_dynamic_requests_go_to_the_network(
    worker: dict[str, Any], request_line: str
) -> None:
    assert dict(worker["routes"])[request_line] == "network"


@needs_node
def test_the_worker_does_not_even_answer_network_requests(worker: dict[str, Any]) -> None:
    untouched = worker["untouched"]

    assert all(not_answered for _, not_answered in untouched["requests"])
    assert untouched["fetched"] == []
    assert untouched["caches"] == []


@needs_node
def test_pages_are_never_stored(worker: dict[str, Any]) -> None:
    routes = dict(worker["routes"])
    assert routes["GET / navigate"] == routes["GET /admin navigate"] == "page"

    online = worker["pages"]["online"]

    assert online["body"] == "net:https://app.test/library"
    assert online["caches"] == {}


@needs_node
def test_a_page_without_a_network_is_the_offline_page(worker: dict[str, Any]) -> None:
    offline = worker["pages"]["offline"]
    device_offline = worker["pages"]["deviceOffline"]

    assert offline["fetched"] == ["https://app.test/library"]
    assert offline["body"] == "net:https://app.test/offline"
    # The device already knows it's offline: no attempt, the offline page at once.
    assert device_offline["fetched"] == []
    assert device_offline["body"] == "net:https://app.test/offline"


# --- precache, versions, other strategies -------------------------------------------


@needs_node
def test_install_precaches_the_config_and_waits(worker: dict[str, Any]) -> None:
    config = worker["config"]
    cached = worker["install"]["caches"]

    assert list(cached) == [f"wn-static-{config['version']}"]
    assert cached[f"wn-static-{config['version']}"] == [
        f"https://app.test{url}" for url in config["precache"]
    ]
    assert worker["install"]["skipWaiting"] == 0


@needs_node
def test_activate_drops_only_older_precaches(worker: dict[str, Any]) -> None:
    version = worker["config"]["version"]

    assert worker["activate"]["caches"] == [
        f"wn-static-{version}",
        "wn-images",
        "wn-fonts",
        "offline-titles",
    ]
    assert worker["activate"]["claim"] == 1


@needs_node
def test_only_skip_waiting_activates_a_waiting_worker(worker: dict[str, Any]) -> None:
    assert worker["messages"] == {"ignored": 0, "skipWaiting": 1}


@needs_node
def test_versioned_static_files_come_from_the_cache(worker: dict[str, Any]) -> None:
    static = worker["static"]

    assert static["hitFetched"] == []
    assert static["missFetches"] == 1
    assert static["missBodies"][0] == static["missBodies"][1]


@needs_node
def test_the_image_cache_keeps_the_newest_hundred(worker: dict[str, Any]) -> None:
    assert worker["images"] == {
        "count": 100,
        "first": "https://app.test/static/img/5.png",
        "last": "https://app.test/static/img/104.png",
    }


@needs_node
def test_a_failed_font_response_is_not_cached(worker: dict[str, Any]) -> None:
    fonts = worker["fonts"]

    assert fonts["cached"] == [
        "https://fonts.gstatic.com/s/manrope/v1/a.woff2",
        "https://fonts.googleapis.com/css2?family=Manrope",
    ]
    assert fonts["refetchedFontFile"] == 0


# --- the update prompt (sw-register.js) ----------------------------------------------


@needs_node
def test_registers_the_worker_for_the_whole_site(register: dict[str, Any]) -> None:
    assert register["waiting"]["registered"] == {"url": "/service-worker.js", "scope": "/"}


@needs_node
def test_a_waiting_version_is_offered_not_swapped_in(register: dict[str, Any]) -> None:
    waiting = register["waiting"]

    assert waiting["offered"] is True
    assert waiting["text"] == "Доступна новая версия"
    assert waiting["buttons"] == ["Обновить", "Позже"]
    assert waiting["messages"] == []
    assert waiting["reloads"] == 0


@needs_node
def test_update_activates_the_waiting_worker_and_reloads(register: dict[str, Any]) -> None:
    update = register["update"]

    assert update["messages"] == [{"type": "SKIP_WAITING"}]
    assert update["applyDisabled"] is True
    assert update["reloads"] == 1


@needs_node
def test_later_hides_the_offer_for_the_session(register: dict[str, Any]) -> None:
    assert register["later"]["toastRemoved"] is True
    assert register["later"]["storage"] == {"swUpdateLater": "1"}
    assert register["later"]["messages"] == []
    assert register["postponed"]["offered"] is False


@needs_node
def test_nothing_is_offered_without_an_old_version_to_replace(register: dict[str, Any]) -> None:
    assert register["nothingWaiting"]["offered"] is False
    # First-ever worker: clients.claim() changes the controller, but nothing reloads.
    assert register["firstInstall"]["offered"] is False
    assert register["firstInstall"]["reloads"] == 0
    assert register["claimedWithoutTap"]["reloads"] == 0

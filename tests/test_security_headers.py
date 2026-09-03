"""Baseline security response headers (PR 189, app/security_headers.py)."""

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

client = TestClient(app)


def test_every_response_carries_the_fixed_security_headers() -> None:
    # /health is as plain a route as this app has - if the middleware is wired up at
    # all, it must show up here, not just on hand-picked "important" pages.
    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_csp_has_no_unsafe_inline_for_scripts() -> None:
    # The three inline <script> blocks the templates used to have were moved to their
    # own static files in this same PR specifically so script-src could stay strict.
    response = client.get("/health")

    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp.split("style-src", 1)[0]


def test_csp_style_src_keeps_unsafe_inline_for_dynamic_progress_bar_widths() -> None:
    # Explicit, documented exception (see app/security_headers.py's module docstring) -
    # several templates set style="width: {{ progress_percent }}%" per row.
    response = client.get("/health")

    csp = response.headers["Content-Security-Policy"]
    assert "style-src 'self' 'unsafe-inline'" in csp


def test_csp_allows_hotlinked_cover_and_chapter_image_hosts() -> None:
    response = client.get("/health")

    csp = response.headers["Content-Security-Policy"]
    assert "img-src 'self' https://ranobelib.me https://*.cdnlibs.org" in csp


def test_csp_denies_framing() -> None:
    response = client.get("/health")

    csp = response.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp


def test_hsts_absent_outside_production(monkeypatch) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    get_settings.cache_clear()

    response = client.get("/health")

    assert "Strict-Transport-Security" not in response.headers

    get_settings.cache_clear()


def test_hsts_present_in_production(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    get_settings.cache_clear()

    response = client.get("/health")

    assert response.headers["Strict-Transport-Security"] == "max-age=63072000; includeSubDomains"

    monkeypatch.delenv("ENVIRONMENT", raising=False)
    get_settings.cache_clear()

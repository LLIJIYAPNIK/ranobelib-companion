"""Baseline security response headers (PR 189, app/security_headers.py)."""

from fastapi.testclient import TestClient

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

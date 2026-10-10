"""PR 326: every /admin* route, discovered from the app itself, is closed without the admin
login - and a new route that isn't classified here fails this file until it is."""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from tests.auth_helpers import register
from tests.test_admin_auth import PASSWORD, Clock, _client

# (method, path) -> status without an admin login (with the panel enabled). Only the
# login form itself may answer 200; login/logout are refused for the missing CSRF token
# before anything else happens; every data page - and every POST that needs the admin
# (PR 342 on) - sends you to the login form before its handler runs.
EXPECTED_WITHOUT_ADMIN = {
    ("GET", "/admin"): 303,
    ("GET", "/admin/tables"): 303,
    ("GET", "/admin/tables/{name}"): 303,
    ("GET", "/admin/_kit"): 303,
    ("POST", "/admin/_kit/demo"): 303,
    ("GET", "/admin/login"): 200,
    ("POST", "/admin/login"): 403,
    ("POST", "/admin/logout"): 403,
}


def _routes(routes: list) -> Iterator:
    # FastAPI 0.14x keeps each included router behind a wrapper in app.routes - unwrap it.
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _routes(inner.routes)
        else:
            yield route


def _admin_routes() -> set[tuple[str, str]]:
    from app.main import app

    return {
        (method, route.path)
        for route in _routes(app.routes)
        if re.match(r"^/admin(/|$)", getattr(route, "path", ""))
        for method in getattr(route, "methods", ()) or ()
        if method != "HEAD"
    }


def test_admin_routes_are_not_in_the_public_api_schema(client: TestClient) -> None:
    # /openapi.json and /docs are public - listing /admin there would announce the panel.
    schema = client.get("/openapi.json").json()
    assert not [path for path in schema["paths"] if path.startswith("/admin")]


def _url(path: str) -> str:
    return path.replace("{name}", "users")


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    import app.auth.admin as admin_auth

    fake = Clock()
    monkeypatch.setattr(admin_auth, "_now", fake)
    monkeypatch.setattr(admin_auth, "_failures", {})
    return fake


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock) -> Iterator[TestClient]:
    with _client(monkeypatch, tmp_path, ADMIN_PASSWORD=PASSWORD) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_every_admin_route_is_classified() -> None:
    assert _admin_routes() == set(EXPECTED_WITHOUT_ADMIN)


def test_without_admin_password_every_admin_route_is_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock
) -> None:
    with _client(monkeypatch, tmp_path, ADMIN_PASSWORD="") as client:
        for method, path in _admin_routes():
            response = client.request(method, _url(path), follow_redirects=False)
            assert response.status_code == 404, (method, path)
    get_settings.cache_clear()


@pytest.mark.parametrize(("method", "path"), sorted(EXPECTED_WITHOUT_ADMIN))
def test_without_the_admin_login_nothing_is_served(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, _url(path), follow_redirects=False)

    assert response.status_code == EXPECTED_WITHOUT_ADMIN[(method, path)]
    if response.status_code == 303:
        assert response.headers["location"] == "/admin/login"
    assert "<table" not in response.text  # no data, not even a fragment of it


@pytest.mark.parametrize(("method", "path"), sorted(EXPECTED_WITHOUT_ADMIN))
def test_a_site_user_is_not_an_admin(client: TestClient, method: str, path: str) -> None:
    register(client, "alice@example.com", "hunter2pass")

    response = client.request(method, _url(path), follow_redirects=False)

    assert response.status_code == EXPECTED_WITHOUT_ADMIN[(method, path)]


def test_admin_pages_keep_the_sites_security_headers(client: TestClient) -> None:
    baseline = client.get("/health").headers
    for path in ("/admin/login", "/admin"):
        headers = client.get(path, follow_redirects=False).headers
        for name in ("content-security-policy", "x-frame-options", "x-content-type-options"):
            assert headers[name] == baseline[name], (path, name)


def test_nothing_on_the_site_points_at_the_admin_panel(client: TestClient) -> None:
    # No robots.txt/sitemap (a "Disallow: /admin" would itself advertise the panel) -
    # admin responses carry X-Robots-Tag: noindex instead - and no page links to it.
    assert client.get("/robots.txt").status_code == 404
    assert client.get("/sitemap.xml").status_code == 404
    templates = Path(__file__).parents[1] / "app/templates"
    for template in templates.rglob("*.html"):
        if template.parent.name == "admin":
            continue
        assert "/admin" not in template.read_text(encoding="utf-8"), template.name

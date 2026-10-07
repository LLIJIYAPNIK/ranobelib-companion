"""PR 324: the /admin panel's own login - config, session, CSRF, throttling."""

import logging
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.auth.admin as admin_auth
from app.config import get_settings
from tests.auth_helpers import register
from tests.db_reset import reset_app_database

PASSWORD = "correct horse battery staple"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(admin_auth, "_now", fake)
    monkeypatch.setattr(admin_auth, "_failures", {})
    return fake


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **env: str) -> TestClient:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))
    for key in ("ADMIN_PASSWORD", "ENVIRONMENT"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    reset_app_database(monkeypatch)
    get_settings.cache_clear()
    from app.main import app

    return TestClient(app)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock) -> Iterator[TestClient]:
    with _client(monkeypatch, tmp_path, ADMIN_PASSWORD=PASSWORD) as test_client:
        yield test_client
    get_settings.cache_clear()


def _csrf(client: TestClient, path: str = "/admin/login") -> str:
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


def _login(client: TestClient, password: str = PASSWORD, **kwargs):
    return client.post(
        "/admin/login",
        data={"password": password, "csrf": _csrf(client)},
        follow_redirects=False,
        **kwargs,
    )


# --- panel off ---------------------------------------------------------------------------


@pytest.mark.parametrize("environment", ["development", "production"])
def test_without_a_password_every_admin_url_is_an_ordinary_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock, environment: str
) -> None:
    with _client(monkeypatch, tmp_path, ADMIN_PASSWORD="", ENVIRONMENT=environment) as client:
        unknown = client.get("/definitely-not-a-page")
        for method, path in (
            ("GET", "/admin"),
            ("GET", "/admin/login"),
            ("POST", "/admin/login"),
            ("POST", "/admin/logout"),
            ("GET", "/admin/anything"),
        ):
            response = client.request(method, path, data={"password": PASSWORD})
            assert response.status_code == 404, (method, path)
            assert response.json() == unknown.json()
            assert "x-robots-tag" not in response.headers
    get_settings.cache_clear()


# --- logging in ---------------------------------------------------------------------------


def test_admin_pages_send_you_to_the_login_form(client: TestClient) -> None:
    response = client.get("/admin", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_wrong_password_is_refused_without_echoing_it(client: TestClient) -> None:
    response = _login(client, "not the password at all")

    assert response.status_code == 401
    assert "Неверный пароль" in response.text
    assert "not the password at all" not in response.text
    assert client.get("/admin", follow_redirects=False).status_code == 303


def test_right_password_opens_the_panel(client: TestClient) -> None:
    response = _login(client)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    home = client.get("/admin")
    assert home.status_code == 200
    assert "Вход выполнен" in home.text
    assert PASSWORD not in home.text
    assert client.get("/admin/login", follow_redirects=False).headers["location"] == "/admin"


def test_admin_responses_are_never_cached_or_indexed(client: TestClient) -> None:
    for response in (client.get("/admin/login"), client.get("/admin", follow_redirects=False)):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert 'name="robots" content="noindex, nofollow"' in client.get("/admin/login").text


def test_the_admin_session_expires(client: TestClient, clock: Clock) -> None:
    _login(client)
    clock.now += get_settings().admin_session_ttl - 1
    assert client.get("/admin").status_code == 200

    clock.now += 2

    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_changing_the_password_ends_existing_admin_sessions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(client)
    monkeypatch.setenv("ADMIN_PASSWORD", "a brand new password")
    get_settings.cache_clear()

    assert client.get("/admin", follow_redirects=False).status_code == 303


def test_logout_ends_the_admin_session(client: TestClient) -> None:
    _login(client)
    csrf = _csrf(client, "/admin")

    response = client.post("/admin/logout", data={"csrf": csrf}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
    assert client.get("/admin", follow_redirects=False).status_code == 303


@pytest.mark.parametrize("token", ["", "forged"])
def test_posts_need_the_sessions_csrf_token(client: TestClient, token: str) -> None:
    client.get("/admin/login")

    login = client.post("/admin/login", data={"password": PASSWORD, "csrf": token})
    assert login.status_code == 403
    assert client.get("/admin", follow_redirects=False).status_code == 303

    _login(client)
    logout = client.post("/admin/logout", data={"csrf": token})
    assert logout.status_code == 403
    assert client.get("/admin").status_code == 200


def test_the_csrf_token_changes_on_login(client: TestClient) -> None:
    before = _csrf(client)
    _login(client)

    assert _csrf(client, "/admin") != before


def test_admin_and_site_user_are_unrelated(client: TestClient) -> None:
    # An admin login isn't a user account...
    _login(client)
    assert 'data-role="locked-feature"' in client.get("/settings/account").text

    # ...and leaving the panel doesn't log the site user out.
    register(client, "alice@example.com", "hunter2pass")
    client.post("/admin/logout", data={"csrf": _csrf(client, "/admin")})
    assert 'value="alice@example.com"' in client.get("/settings/account").text


def test_the_password_never_reaches_the_logs(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    _login(client, "wrong-guess-123")
    _login(client)

    assert "wrong-guess-123" not in caplog.text
    assert PASSWORD not in caplog.text


# --- throttling ---------------------------------------------------------------------------


def test_repeated_failures_lock_the_ip_out_and_the_lock_grows(
    client: TestClient, clock: Clock
) -> None:
    for _ in range(3):
        assert _login(client, "nope").status_code == 401

    fourth = _login(client, "nope")
    assert fourth.status_code == 429
    assert fourth.headers["retry-after"] == "15"
    assert "Слишком много попыток" in fourth.text

    # Locked: even the right password is refused without being checked.
    assert _login(client).status_code == 429

    clock.now += 16
    fifth = _login(client, "nope")
    assert fifth.status_code == 429
    assert fifth.headers["retry-after"] == "30"


def test_success_after_the_lock_clears_the_count(client: TestClient, clock: Clock) -> None:
    for _ in range(4):
        _login(client, "nope")
    clock.now += 16

    assert _login(client).status_code == 303
    client.post("/admin/logout", data={"csrf": _csrf(client, "/admin")})
    assert _login(client, "nope").status_code == 401  # back to the free attempts


def test_other_ips_are_not_locked(client: TestClient) -> None:
    for _ in range(4):
        _login(client, "nope")

    other = TestClient(client.app, client=("203.0.113.9", 50000))
    assert _login(other).status_code == 303

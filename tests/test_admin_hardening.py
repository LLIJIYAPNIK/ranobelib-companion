"""PR 355: the admin panel's final security pass - every value a request sends is bounded,
every POST needs the CSRF token, every change is rate-limited and lands in the action log,
every exports is rate-limited, every database read behind a page runs read-only under the
admin timeout, the CSP and noindex hold on every page, and neither the admin's searches nor
the site's emailed tokens reach the access log. (Without ADMIN_PASSWORD every route is a
404: tests/test_admin_routes.py.)"""

import inspect
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.api.admin as admin_api
from app.access_log import AccessLogRedactor, redact
from app.auth.admin import (
    ADMIN_EXPORTS_PER_MINUTE,
    ADMIN_WRITES_PER_MINUTE,
    limit_admin_exports,
    limit_admin_writes,
)
from app.config import get_settings
from app.db.admin_data import QUERY_TIMEOUT_MS
from app.db.comments import create_comment
from app.db.connection import connection
from app.db.users import get_user_by_email
from app.services.probe import ProbeResult
from tests.auth_helpers import register
from tests.test_admin_auth import PASSWORD, Clock, _client, _csrf, _login
from tests.test_admin_routes import EXPECTED_WITHOUT_ADMIN, _routes
from tests.test_admin_shell import _dependency_calls

# The POSTs that only start or end the admin's own session.
SESSION_POSTS = {"/admin/login", "/admin/logout"}


def _admin_routes() -> list[APIRoute]:
    from app.main import app

    return [
        route
        for route in _routes(app.routes)
        if isinstance(route, APIRoute) and re.match(r"^/admin(/|$)", route.path)
    ]


def _changes(route: APIRoute) -> bool:
    """A POST that changes data - everything but the session's own and the dev showcase."""
    return "POST" in route.methods and route.path not in SESSION_POSTS and "/_kit" not in route.path


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


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    assert _login(client).status_code == 303
    return client


# --- input bounds -------------------------------------------------------------------------


def test_every_value_a_request_sends_is_bounded() -> None:
    unbounded = []
    for route in _admin_routes():
        dependant = route.dependant
        for param in dependant.query_params + dependant.path_params + dependant.body_params:
            kinds = {type(item).__name__ for item in param.field_info.metadata}
            annotation = str(param.field_info.annotation)
            needed = {"Le"} if "int" in annotation else {"MaxLen"}
            if not needed <= kinds:
                unbounded.append(f"{route.path} {param.name}")
    assert unbounded == []


def test_an_oversized_value_is_refused(admin: TestClient) -> None:
    for path, params in (
        ("/admin/users", {"order": "x" * 21}),
        ("/admin/tables/users", {"sort": "x" * 64}),
        ("/admin/users", {"q": "x" * 201}),
    ):
        assert admin.get(path, params=params).status_code == 422, (path, params)
    assert admin.get("/admin/tables/" + "x" * 64).status_code == 422


# --- CSRF, rate limit, action log ---------------------------------------------------------


def _url(path: str) -> str:
    return (
        path.replace("{user_id}", "1")
        .replace("{comment_id}", "1")
        .replace("{target_type}", "comment")
        .replace("{target_id}", "1")
    )


def test_every_post_refuses_a_missing_or_wrong_csrf_token(admin: TestClient) -> None:
    posts = [route for route in _admin_routes() if "POST" in route.methods]
    assert len(posts) >= 10
    for route in posts:
        for token in ("", "not-the-token"):
            response = admin.post(_url(route.path), data={"csrf": token}, follow_redirects=False)
            assert response.status_code == 403, (route.path, token)
    # The session survived all of that - the refusals changed nothing.
    assert admin.get("/admin", follow_redirects=False).status_code == 200


def test_every_change_and_export_is_rate_limited() -> None:
    for route in _admin_routes():
        calls = set(_dependency_calls(route.dependant))
        if _changes(route):
            assert limit_admin_writes in calls, route.path
        if route.path.endswith("/csv"):
            assert limit_admin_exports in calls, route.path


def test_every_change_goes_into_the_action_log() -> None:
    for route in _admin_routes():
        if _changes(route):
            source = inspect.getsource(route.endpoint)
            assert "admin_audit.record_change(" in source, route.path


def test_too_many_changes_in_a_minute_are_refused(admin: TestClient) -> None:
    token = _csrf(admin, "/admin")
    statuses = [
        admin.post(
            "/admin/comments/999999/hide", data={"csrf": token}, follow_redirects=False
        ).status_code
        for _ in range(ADMIN_WRITES_PER_MINUTE + 1)
    ]

    assert statuses[:-1] == [404] * ADMIN_WRITES_PER_MINUTE  # no such comment
    assert statuses[-1] == 429
    refused = admin.post("/admin/users/1/sessions", data={"csrf": token}, follow_redirects=False)
    assert refused.status_code == 429 and refused.headers["retry-after"] == "60"
    # Reading isn't limited.
    assert admin.get("/admin/comments").status_code == 200


def test_too_many_exports_in_a_minute_are_refused(admin: TestClient) -> None:
    statuses = [
        admin.get("/admin/audit/csv").status_code for _ in range(ADMIN_EXPORTS_PER_MINUTE + 1)
    ]

    assert statuses[:-1] == [200] * ADMIN_EXPORTS_PER_MINUTE
    assert statuses[-1] == 429
    assert admin.get("/admin/tables/users/csv").status_code == 429


def test_a_stranger_gets_the_login_form_not_a_429(client: TestClient) -> None:
    for _ in range(ADMIN_WRITES_PER_MINUTE + 2):
        response = client.post("/admin/users/1/sessions", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/admin/login"


# --- every admin page: read-only reads under the timeout, CSP, noindex --------------------


async def _seed(client: TestClient) -> tuple[int, int]:
    register(client, "alice@example.com", "hunter2pass", nickname="aster")
    client.post("/logout")
    async with connection() as conn:
        user = await get_user_by_email(conn, "alice@example.com")
        comment = await create_comment(conn, user.id, "1--some-title", "1", "5", "", 0, "Текст")
    return user.id, comment.id


def _pages(user_id: int, comment_id: int) -> list[str]:
    """Every admin GET a browser opens, filled in - the drawers open too."""
    pages = []
    for method, path in sorted(EXPECTED_WITHOUT_ADMIN):
        if method != "GET" or path in ("/admin/login", "/admin/search/suggest"):
            continue
        pages.append(path.replace("{name}", "users").replace("{slug_url}", "1--some-title"))
    return [
        *pages,
        f"/admin/users?user={user_id}",
        f"/admin/comments?comment={comment_id}",
        "/admin/search?q=aster",
        "/admin/search/suggest?q=aster",
    ]


@pytest.fixture
def no_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing reaches ranobelib.me: the pages get no title data, the probe a fixed answer."""

    async def nothing(*args: object, **kwargs: object) -> dict:
        return {}

    async def no_details(*args: object) -> SimpleNamespace:
        return SimpleNamespace(volumes=[], name="Тайтл", cover_url=None, slug_url="1--some-title")

    async def probe() -> ProbeResult:
        from datetime import UTC, datetime

        from app.services import probe as probe_module

        return ProbeResult(probe_module.OK, 100.0, datetime.now(UTC))

    async def no_quote(*args: object) -> None:
        return None

    monkeypatch.setattr(admin_api, "title_summaries", nothing)
    monkeypatch.setattr(admin_api, "tables_of_contents", nothing)
    monkeypatch.setattr(admin_api, "title_details", no_details)
    monkeypatch.setattr(admin_api, "probe_ranobelib", probe)
    monkeypatch.setattr(admin_api, "paragraph_text", no_quote)


async def test_every_read_behind_an_admin_page_is_read_only_with_the_timeout(
    client: TestClient, no_sdk: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id, comment_id = await _seed(client)
    _login(client)
    original = psycopg.AsyncConnection.execute
    seen: list[tuple[str, str, str]] = []
    checking = False

    async def execute(self, query, params=None, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal checking
        text = query if isinstance(query, str) else query.as_string(self)
        reads = re.match(r"\s*(SELECT|WITH)\b", text, re.I) and "set_config" not in text
        if reads and not checking:
            checking = True
            try:
                cursor = await original(
                    self,
                    "SELECT current_setting('transaction_read_only') AS ro, "
                    "current_setting('statement_timeout') AS timeout",
                )
                row = await cursor.fetchone()
                seen.append((" ".join(text.split())[:80], row["ro"], row["timeout"]))
            finally:
                checking = False
        return await original(self, query, params, **kwargs)

    monkeypatch.setattr(psycopg.AsyncConnection, "execute", execute)
    for page in _pages(user_id, comment_id):
        assert client.get(page).status_code == 200, page
    monkeypatch.undo()

    assert len(seen) > 30
    timeout = f"{QUERY_TIMEOUT_MS // 1000}s"
    loose = [(query, ro, t) for query, ro, t in seen if (ro, t) != ("on", timeout)]
    assert loose == []


async def test_every_admin_page_keeps_the_csp_and_noindex(client: TestClient, no_sdk: None) -> None:
    user_id, comment_id = await _seed(client)
    site_csp = client.get("/health").headers["content-security-policy"]
    _login(client)

    assert "script-src 'self'" in site_csp
    for page in _pages(user_id, comment_id):
        response = client.get(page)
        csp = response.headers["content-security-policy"]
        assert csp == site_csp, page  # the panel's is the site's - nothing loosened for it
        script_src = re.search(r"script-src ([^;]+)", csp).group(1)
        assert "unsafe" not in script_src and "http" not in script_src, page
        assert response.headers["x-robots-tag"] == "noindex, nofollow", page
        assert response.headers["cache-control"] == "no-store", page
        if response.headers["content-type"].startswith("text/html"):
            assert '<meta name="robots" content="noindex, nofollow">' in response.text, page


# --- logs ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "logged"),
    [
        ("/admin/users?q=alice%40example.com", "/admin/users"),
        ("/admin/search/suggest?q=aster", "/admin/search/suggest"),
        ("/admin?period=7d", "/admin"),
        ("/verify-email/AbC123secret", "/verify-email/…"),
        ("/password-reset/AbC123secret?x=1", "/password-reset/…?x=1"),
        ("/titles/1--x?branch_id=5", "/titles/1--x?branch_id=5"),  # the site's own, as is
        ("/administrator", "/administrator"),
    ],
)
def test_the_access_log_drops_admin_queries_and_emailed_tokens(path: str, logged: str) -> None:
    assert redact(path) == logged


def test_the_redaction_is_on_uvicorns_access_logger(caplog: pytest.LogCaptureFixture) -> None:
    import app.main  # noqa: F401 - installs the filter

    logger = logging.getLogger("uvicorn.access")
    assert any(isinstance(item, AccessLogRedactor) for item in logger.filters)
    record = logger.makeRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/admin/users?q=alice@example.com", "1.1", 200),
        None,
    )
    for item in logger.filters:
        item.filter(record)
    assert record.getMessage() == '127.0.0.1:5000 - "GET /admin/users HTTP/1.1" 200'


def test_admin_log_messages_carry_no_user_data() -> None:
    # The panel's own log lines name what failed, never whose data or a value from it.
    source = Path(admin_api.__file__).read_text("utf-8")
    calls = re.findall(r"logger\.\w+\((.*?)\)\n", source, re.S)
    assert calls
    for call in calls:
        assert not re.search(r"email|nickname|password|token|body|\bq\b|query", call), call

"""The /admin panel's own login (PR 324) - not a user account, nothing in the database.

One password from the environment (``ADMIN_PASSWORD``, app/config.py). Without it the
panel doesn't exist: ``require_admin_enabled`` answers every /admin* route with the same
404 an unknown URL gets.

An admin login is a separate entry in the site's signed session cookie (the same one
``RememberMeSessionMiddleware`` already signs - tamper-proof, httponly, Secure in
production), holding:

- ``until`` - when it expires (``ADMIN_SESSION_TTL_SECONDS``, 2 h by default), checked
  server-side on every request, independent of the cookie's own lifetime;
- ``fp`` - a fingerprint of the current password, so changing ``ADMIN_PASSWORD`` ends
  every admin session issued under the old one.

It never touches ``user_id``: being an admin and being logged in as a site user are
unrelated, and logging out of one leaves the other as it was. Every admin POST carries a
per-session CSRF token. The password itself is only ever compared
(``hmac.compare_digest``), never stored, logged or echoed back.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request

from app.config import get_settings

ADMIN_SESSION_KEY = "admin"
CSRF_SESSION_KEY = "admin_csrf"
LOGIN_PATH = "/admin/login"


def _now() -> float:
    return time.time()


def _fingerprint() -> str:
    settings = get_settings()
    return hmac.new(
        settings.session_secret_key.encode(),
        (settings.admin_password or "").encode(),
        hashlib.sha256,
    ).hexdigest()[:32]


def require_admin_enabled() -> None:
    """Router-level dependency: no ADMIN_PASSWORD, no panel - a plain 404."""
    if not get_settings().admin_enabled:
        raise HTTPException(status_code=404)


def password_matches(candidate: str) -> bool:
    expected = get_settings().admin_password
    if not expected:
        return False
    return hmac.compare_digest(candidate.encode(), expected.encode())


def start_admin_session(request: Request) -> None:
    request.session[ADMIN_SESSION_KEY] = {
        "until": _now() + get_settings().admin_session_ttl,
        "fp": _fingerprint(),
    }
    # A fresh token after login, so one seen before it can't be replayed after.
    request.session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)


def end_admin_session(request: Request) -> None:
    request.session.pop(ADMIN_SESSION_KEY, None)
    request.session.pop(CSRF_SESSION_KEY, None)


def is_admin(request: Request) -> bool:
    data = request.session.get(ADMIN_SESSION_KEY)
    if not isinstance(data, dict):
        return False
    until = data.get("until")
    if not isinstance(until, (int, float)) or until <= _now():
        return False
    return hmac.compare_digest(str(data.get("fp", "")).encode(), _fingerprint().encode())


def require_admin(request: Request) -> None:
    """Dependency for every admin page: no valid admin login -> the login form."""
    if not is_admin(request):
        request.session.pop(ADMIN_SESSION_KEY, None)
        raise HTTPException(status_code=303, headers={"Location": LOGIN_PATH})


def csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def check_csrf(request: Request, submitted: str) -> None:
    expected = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(expected, str) or not hmac.compare_digest(
        submitted.encode(), expected.encode()
    ):
        raise HTTPException(status_code=403)

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


# --- login throttling (per IP) ---------------------------------------------------------
# In-memory, process-wide - same single-process MVP treatment as app/auth/rate_limit.py.
# The first _FREE_FAILURES wrong passwords cost nothing; every one after that locks the IP
# out for _BASE_LOCK_SECONDS * 2^(n - _FREE_FAILURES - 1), capped at _MAX_LOCK_SECONDS.
# While locked, the password isn't even checked - a correct guess doesn't get through
# either. A success clears the IP's count; an IP quiet for _FORGET_AFTER is forgotten.
_FREE_FAILURES = 3
_BASE_LOCK_SECONDS = 15.0
_MAX_LOCK_SECONDS = 60 * 60.0
_FORGET_AFTER = 24 * 60 * 60.0

# ip -> (failures, locked_until, last_failure)
_failures: dict[str, tuple[int, float, float]] = {}


def client_ip(request: Request) -> str:
    """The address login throttling counts by - and the action log records (PR 343)."""
    return request.client.host if request.client is not None else "unknown"


def login_locked_for(ip: str) -> float:
    """Seconds this IP must still wait before its next login attempt (0 = go ahead)."""
    entry = _failures.get(ip)
    if entry is None:
        return 0.0
    return max(0.0, entry[1] - _now())


def record_failed_login(ip: str) -> None:
    now = _now()
    for stale in [key for key, (_, _, last) in _failures.items() if now - last > _FORGET_AFTER]:
        del _failures[stale]
    count = _failures.get(ip, (0, 0.0, 0.0))[0] + 1
    locked_until = 0.0
    if count > _FREE_FAILURES:
        lock = _BASE_LOCK_SECONDS * 2 ** (count - _FREE_FAILURES - 1)
        locked_until = now + min(lock, _MAX_LOCK_SECONDS)
    _failures[ip] = (count, locked_until, now)


def reset_failed_logins(ip: str) -> None:
    _failures.pop(ip, None)


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

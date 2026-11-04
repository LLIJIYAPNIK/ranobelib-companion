"""The visitor's cookie choice (PR 375) - «Принять все» or «Только необходимые».

The site sets only its own cookies and no third-party, analytics or advertising ones:

- ``session`` - signing in; strictly necessary, set only when the visitor signs in.
- ``cookie_consent`` - this choice itself; strictly necessary (without it the question
  would come back on every page).
- ``recent_titles`` - the «Недавние» history (app/recent_titles.py); optional, written
  only after «Принять все».

Before any choice the optional cookie isn't written either: declining is the default, and
accepting is the visitor's own act. «Только необходимые» also deletes a ``recent_titles``
already on the device - it's httponly, so only the server can.
"""

from typing import Literal

from starlette.requests import Request
from starlette.responses import Response

CONSENT_COOKIE = "cookie_consent"
ALL: Literal["all"] = "all"
ESSENTIAL: Literal["essential"] = "essential"
CHOICES = (ALL, ESSENTIAL)
# A year: long enough not to ask again soon, short enough to ask again some day.
CONSENT_MAX_AGE_SECONDS = 365 * 24 * 60 * 60


def read_consent(request: Request) -> str | None:
    """ "all", "essential", or None when the visitor hasn't chosen (or the value is junk)."""
    value = request.cookies.get(CONSENT_COOKIE)
    return value if value in CHOICES else None


def optional_cookies_allowed(request: Request) -> bool:
    return read_consent(request) == ALL


def write_consent(response: Response, choice: str, *, secure: bool) -> None:
    from app.recent_titles import COOKIE_NAME as RECENT_COOKIE

    response.set_cookie(
        CONSENT_COOKIE,
        choice,
        max_age=CONSENT_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    if choice == ESSENTIAL:
        response.delete_cookie(RECENT_COOKIE, httponly=True, samesite="lax")

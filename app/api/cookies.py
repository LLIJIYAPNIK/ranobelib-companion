"""Cookies and the data kept on the device (PR 375): the visitor's choice and the
«Настройки → Данные и cookie» page that says what is stored, why and for how long.

App-level only - nothing here is about ranobe or the SDK."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.config import get_settings
from app.cookie_consent import CHOICES, CONSENT_MAX_AGE_SECONDS, read_consent, write_consent
from app.recent_titles import MAX_AGE_SECONDS as RECENT_MAX_AGE_SECONDS
from app.recent_titles import MAX_ENTRIES as RECENT_MAX_ENTRIES
from app.templating import templates

router = APIRouter()


@router.get("/settings/cookies")
async def settings_cookies_page(request: Request) -> HTMLResponse:
    """The full list - every cookie and every kind of on-device storage, with what it holds,
    what for and how long - and the choice, changeable at any time. Guests get it too:
    cookies belong to the browser, not to an account. The lifetimes come from the config
    and the modules that set the cookies, so the page can't drift from what they do."""
    settings = get_settings()
    day = 24 * 60 * 60
    return templates.TemplateResponse(
        request,
        "settings_cookies.html",
        {
            "active_nav": "settings",
            "active_settings_section": "cookies",
            "consent": read_consent(request),
            "session_days": settings.session_max_age // day,
            "remember_days": settings.session_remember_max_age // day,
            "recent_days": RECENT_MAX_AGE_SECONDS // day,
            "recent_entries": RECENT_MAX_ENTRIES,
            "consent_days": CONSENT_MAX_AGE_SECONDS // day,
        },
    )


@router.post("/cookies/consent", response_model=None)
async def save_cookie_consent(
    request: Request,
    choice: Annotated[str, Form()],
    next_url: Annotated[str, Form(alias="next")] = "/",
) -> Response:
    """«Принять все» / «Только необходимые» - from the notice or the settings page.

    cookie-notice.js posts it with fetch and gets a 204; without the script it is a plain
    form, answered with a redirect back to the page it was sent from (a path on this site
    only - never an absolute or protocol-relative URL)."""
    if choice not in CHOICES:
        return Response(status_code=400)
    if request.headers.get("x-requested-with") == "fetch":
        response: Response = Response(status_code=204)
    else:
        safe = next_url.startswith("/") and not next_url.startswith(("//", "/\\"))
        response = RedirectResponse(next_url if safe else "/", status_code=303)
    write_consent(response, choice, secure=get_settings().is_production)
    return response

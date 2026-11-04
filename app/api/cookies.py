"""Cookies and the data kept on the device (PR 375): the visitor's choice and the
«Настройки → Данные и cookie» page that says what is stored, why and for how long.

App-level only - nothing here is about ranobe or the SDK."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from app.config import get_settings
from app.cookie_consent import CHOICES, write_consent

router = APIRouter()


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

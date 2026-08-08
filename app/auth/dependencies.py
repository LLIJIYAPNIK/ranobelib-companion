"""Who's logged in, and requiring that someone is.

Distinct from the SDK's `AuthRequiredError` (paid/early ranobelib.me content, mapped in
app/exceptions.py to a plain "недоступно") - this is our own application login.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.db.connection import get_connection
from app.db.users import User, get_user_by_id


def get_current_user(request: Request) -> User | None:
    """The logged-in user for this request, or None if there isn't one.

    A missing/stale user_id in the session (e.g. the account was deleted) is treated the
    same as no session at all, rather than raising - the next login simply overwrites it.
    """
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    return get_user_by_id(get_connection(), user_id)


def require_current_user(user: Annotated[User | None, Depends(get_current_user)]) -> User:
    """`Depends(require_current_user)` for routes that need a logged-in user - none yet
    in this PR (personal library is PR 14), but this is the primitive PR 14/17/18 attach
    to their routes.

    Redirects anonymous visitors to /login rather than returning a bare 401: this app is
    server-rendered HTML, so a redirect is what a real browser navigation needs. The
    redirect works purely off status code + Location header, independent of the
    HTTPException's JSON body.
    """
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user

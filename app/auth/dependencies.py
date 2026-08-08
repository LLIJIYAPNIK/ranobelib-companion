"""Who's logged in, and requiring that someone is.

Distinct from the SDK's `AuthRequiredError` (paid/early ranobelib.me content, mapped in
app/exceptions.py to a plain "недоступно") - this is our own application login.
"""

from __future__ import annotations

from fastapi import Request

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

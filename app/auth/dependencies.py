"""Who's logged in, and requiring that someone is.

Distinct from the SDK's `AuthRequiredError` (paid/early ranobelib.me content, mapped in
app/exceptions.py to a plain "недоступно") - this is our own application login.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.ban_notice import ban_message
from app.db.bans import active_ban
from app.db.connection import connection
from app.db.users import User, get_user_by_id


async def get_current_user(request: Request) -> User | None:
    """The logged-in user for this request, or None if there isn't one.

    A missing/stale user_id in the session (e.g. the account was deleted) is treated the
    same as no session at all, rather than raising - the next login simply overwrites it.
    A session whose stashed ``session_version`` no longer matches the account's current
    one is treated the same way (PR 225): that happens when the password was reset via
    the "Забыли пароль?" flow after this particular session was issued, which bumps
    ``users.session_version`` specifically to invalidate every session issued before it -
    see app/db/users.py's ``update_user_password_from_reset()``.

    Registered as an app-level dependency (``FastAPI(dependencies=[Depends(get_current_user)])``,
    see app/main.py) so it runs for every request regardless of whether the specific route
    handling it also declares this dependency itself - caching the result on
    ``request.state`` is what lets app/templating.py's Jinja context processor (which,
    unlike a FastAPI dependency, can't itself await a database call) read it back
    synchronously at render time.

    Deliberately checks out its own connection via ``connection()`` (see
    app/db/connection.py) only inside the ``user_id is not None`` branch, rather than
    taking one as a ``Depends(get_connection)`` parameter - a *parameter* is resolved by
    FastAPI unconditionally before this function's body even runs, which would mean this
    app-level dependency checks a connection out of the pool for every single anonymous
    request too, site-wide, for a lookup it was always going to skip anyway.
    """
    request.state.active_ban = None
    request.state.ban_notice = None
    user_id = request.session.get("user_id")
    if user_id is None:
        request.state.current_user = None
        return None
    async with connection() as conn:
        user = await get_user_by_id(conn, user_id)
        if user is not None and user.session_version != request.session.get("session_version"):
            user = None
        # PR 349: a blocked account is a guest for as long as the block lasts - checked
        # on every request, so a block takes effect on the next click and a term that ran
        # out lets the same session back in. The session itself is kept; the block is
        # left on request.state for the notice (the strip in base.html, /account-blocked).
        ban = await active_ban(conn, user.id) if user is not None else None
    if ban is not None and user is not None:
        request.state.active_ban = ban
        request.state.ban_notice = ban_message(ban, user.timezone)
        user = None
    request.state.current_user = user
    return user


def require_current_user(
    request: Request, user: Annotated[User | None, Depends(get_current_user)]
) -> User:
    """`Depends(require_current_user)` for routes that need a logged-in user - none yet
    in this PR (personal library is PR 14), but this is the primitive PR 14/17/18 attach
    to their routes.

    Redirects anonymous visitors to /login rather than returning a bare 401: this app is
    server-rendered HTML, so a redirect is what a real browser navigation needs. The
    redirect works purely off status code + Location header, independent of the
    HTTPException's JSON body.
    """
    if user is None:
        # PR 349: a blocked account is told why, not just asked to log in again.
        blocked = getattr(request.state, "active_ban", None) is not None
        location = "/account-blocked" if blocked else "/login"
        raise HTTPException(status_code=303, headers={"Location": location})
    return user

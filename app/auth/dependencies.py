"""Who's logged in, and requiring that someone is.

Distinct from the SDK's `AuthRequiredError` (paid/early ranobelib.me content, mapped in
app/exceptions.py to a plain "недоступно") - this is our own application login.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.db.connection import connection
from app.db.users import User, get_user_by_id


async def get_current_user(request: Request) -> User | None:
    """The logged-in user for this request, or None if there isn't one.

    A missing/stale user_id in the session (e.g. the account was deleted) is treated the
    same as no session at all, rather than raising - the next login simply overwrites it.

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
    user_id = request.session.get("user_id")
    if user_id is None:
        request.state.current_user = None
        return None
    async with connection() as conn:
        user = await get_user_by_id(conn, user_id)
    request.state.current_user = user
    return user


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

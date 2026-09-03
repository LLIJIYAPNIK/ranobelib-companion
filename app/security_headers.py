"""Baseline security response headers (PR 189).

Not a response to any one concrete vulnerability found in this app - the security audit
that triggered this wave (see CLAUDE.md, roadmap wave 21) found the SQL layer already
parameterized, comment bodies already double-sanitized against XSS
(``app/markdown_render.py``), and the image proxy already domain-allowlisted
(``app/api/images.py``). This is the standard defense-in-depth layer any of that would
otherwise be missing before a real deploy: browser-enforced hardening that costs nothing
when everything else already behaves, and limits the blast radius on the day something
doesn't.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response


def install_security_headers(app: FastAPI) -> None:
    """Registers the middleware that stamps every response with the headers above."""

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        return response

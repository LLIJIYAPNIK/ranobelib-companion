"""Baseline security response headers (PR 189).

Not a response to any one concrete vulnerability found in this app - the security audit
that triggered this wave (see CLAUDE.md, roadmap wave 21) found the SQL layer already
parameterized, comment bodies already double-sanitized against XSS
(``app/markdown_render.py``), and the image proxy already domain-allowlisted
(``app/api/images.py``). This is the standard defense-in-depth layer any of that would
otherwise be missing before a real deploy: browser-enforced hardening that costs nothing
when everything else already behaves, and limits the blast radius on the day something
doesn't.

``Content-Security-Policy`` is the one directive that needed the app to change rather
than just gain a header. ``script-src 'self'`` (no ``'unsafe-inline'``) was reachable
outright: the three inline ``<script>`` blocks the templates used to have (sidebar-
expand-init, cookie-notice-init, scroll-restoration - same commit as this CSP) carried no
per-request/user data, so each moved to its own static file under ``app/static/js/``
without changing behaviour, and now load as ordinary same-origin ``<script src>`` tags
instead. ``style-src`` couldn't get the same treatment without a much bigger change:
several templates set a dynamic ``style="width: {{ progress_percent }}%"`` per-row for
progress bars, and ``settings_layout.html`` has one static ``@view-transition`` block
that CSS can't express with a selector anyway - so ``style-src`` keeps
``'unsafe-inline'`` as an explicit, intentional exception, not an oversight.

``img-src`` allowlists ``ranobelib.me``/``*.cdnlibs.org`` alongside ``'self'`` because
cover and chapter-content images are hotlinked straight from those domains (SDK/API is
read-only, see CLAUDE.md "Что явно не делать" - this app never re-hosts them), the same
two hosts already trusted by the image-download proxy's own allowlist
(``app/api/images.py``'s ``_ALLOWED_HOSTS``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response

_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' https://ranobelib.me https://*.cdnlibs.org; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


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
        response.headers["Content-Security-Policy"] = _CONTENT_SECURITY_POLICY
        return response

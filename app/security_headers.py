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
(``app/api/images.py``'s ``_ALLOWED_HOSTS``). ``blob:`` is there for local previews of a
file the visitor just picked (comment attachment chip, avatar upload) - an object URL
only this page's own script can mint for a file already on the device; ``data:`` stays
out, since any injected markup or CSS could inline an image through it.

``fonts.googleapis.com`` (the stylesheet) and ``fonts.gstatic.com`` (the font files it
points at) are the Aurora Ink redesign's webfonts (PR 247) - Manrope, Literata, Golos
Text, JetBrains Mono - loaded by ``base.html`` through Google's ordinary ``<link>``.

The service worker (PR 328) is the one response with a policy of its own. A worker's CSP
is the one on its script, and ``fetch()`` inside it is ``connect-src`` - which falls back
to ``default-src 'self'``, so the worker couldn't pass the webfonts through (let alone
cache them for offline use) without the two Google Fonts hosts there. Only the worker gets
them: pages never ``fetch()`` those hosts themselves.

``Strict-Transport-Security`` is gated on ``Settings.is_production`` (PR 187) - sending
it unconditionally would tell a browser to force HTTPS for this host, which permanently
breaks a plain ``http://localhost`` dev server until the browser's HSTS cache for it
expires.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response

from app.config import get_settings

_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' blob: https://ranobelib.me https://*.cdnlibs.org; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

_SERVICE_WORKER_PATH = "/service-worker.js"
_SERVICE_WORKER_CONTENT_SECURITY_POLICY = (
    _CONTENT_SECURITY_POLICY
    + "; connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com"
)

_HSTS_VALUE = "max-age=63072000; includeSubDomains"


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
        response.headers["Content-Security-Policy"] = (
            _SERVICE_WORKER_CONTENT_SECURITY_POLICY
            if request.url.path == _SERVICE_WORKER_PATH
            else _CONTENT_SECURITY_POLICY
        )
        if get_settings().is_production:
            response.headers["Strict-Transport-Security"] = _HSTS_VALUE
        return response

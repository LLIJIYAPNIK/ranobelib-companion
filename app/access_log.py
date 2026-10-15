"""What uvicorn's access log may say about a request (PR 355).

By default it writes every request's full path with its query string. Two kinds of URL
must not reach the server log that way:

- the admin panel's (``/admin...``): its query strings are what the admin searched and
  filtered - emails, nicknames, comment text (``/admin/users?q=alice@example.com``, every
  keystroke of the global search). The path stays, the query is dropped;
- the one-time links from the site's emails (``/verify-email/<token>``,
  ``/password-reset/<token>``): the token is a credential until it's used. It's replaced
  with ``…``.

Installed once from app/main.py as a filter on the ``uvicorn.access`` logger, so it holds
however uvicorn is started (Dockerfile, run_windows.py, ``--reload``).
"""

from __future__ import annotations

import logging
import re

ACCESS_LOGGER = "uvicorn.access"
REDACTED = "…"

_TOKEN_PATH = re.compile(r"^(/(?:verify-email|password-reset)/)[^/?#]+")


def redact(path: str) -> str:
    """``path`` (with its query string) as it may appear in the log."""
    if path == "/admin" or path.startswith(("/admin/", "/admin?")):
        return path.split("?", 1)[0]
    return _TOKEN_PATH.sub(rf"\g<1>{REDACTED}", path)


class AccessLogRedactor(logging.Filter):
    """Rewrites the path argument of uvicorn's access record:
    ``'%s - "%s %s HTTP/%s" %d'`` with (client, method, path, http version, status)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], redact(args[2]), *args[3:])
        return True


def install_access_log_redaction() -> None:
    logger = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(f, AccessLogRedactor) for f in logger.filters):
        logger.addFilter(AccessLogRedactor())

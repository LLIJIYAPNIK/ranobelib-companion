"""Application configuration, read from environment variables.

``cache_dir`` backs the SDK's on-disk cache of raw API responses (title metadata,
chapter lists, chapter content), shared across all users since the underlying data is
public. In a container deployment, point it at a persistent volume — a path on the
container's writable layer is wiped on every restart/redeploy, which defeats the point
of caching between requests.

``cache_ttl`` controls how long a cached response is trusted before being re-fetched.
It's set in hours rather than days so a chapter newly published on ranobelib.me becomes
visible on this site within a reasonable window without anyone having to trigger a
manual ``refresh=True``.

``db_path`` is the application's own SQLite database (accounts, personal library,
activity) - separate from ``cache_dir``, which only holds the SDK's public response
cache. Needs the same persistent-volume treatment as ``cache_dir`` in a container
deployment.

``session_secret_key`` signs the session cookie (see ``SessionMiddleware`` in
``app/main.py``). Without an explicit value, a random key is generated per process
start, which invalidates every session on restart - fine for local dev, not for a real
deployment. When ``is_production`` is set this is upgraded from a logged warning to a
startup ``RuntimeError``, since a forgotten environment variable shouldn't silently
degrade into "everyone gets logged out on every deploy" in production.

``environment``/``is_production`` (``ENVIRONMENT`` env var, ``"production"`` or
otherwise) gates production-only behavior that would get in the way of local
development - currently: requiring ``SESSION_SECRET_KEY`` outright instead of just
warning, and setting the session cookie's ``Secure`` flag (``https_only`` on
``RememberMeSessionMiddleware`` in ``app/main.py``), which would break a plain
``http://localhost`` dev server if it applied unconditionally. Unset or any value other
than ``"production"`` keeps today's development behavior.

``session_max_age``/``session_remember_max_age`` are the two session-cookie lifetimes
(in seconds) used by ``RememberMeSessionMiddleware`` (``app/auth/session_middleware.py``,
PR 36): the former for an ordinary login, the latter when "Запомнить меня" was checked.
The default for ``session_max_age`` matches Starlette's own ``SessionMiddleware``
default (14 days) so unchecked behaves exactly as before PR 36.

``download_file_ttl`` is the fallback cleanup window (in seconds) for a whole-title
download's exported file (``app/jobs/store.py``'s ``sweep_expired_result_files()``, PR 50):
normally deleted the moment a visitor actually downloads it (job page or the global
"file ready" toast), this TTL only matters if nobody ever comes back to click it.

``avatar_dir`` holds uploaded profile avatar images (PR 96) - application user data, like
``db_path``, not part of the SDK's public response cache, so it's kept separate from
``cache_dir``. Needs the same persistent-volume treatment in a container deployment,
otherwise avatars vanish on every restart.

``comment_attachment_dir`` holds the converted (GIF -> silent looping mp4, PR 150)
attachments a comment can carry - same "user-generated data outside app/static, needs a
persistent volume" treatment as ``avatar_dir``, just its own directory since a comment
attachment isn't a profile avatar.
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = ".ranobelib_cache"
_DEFAULT_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours
_DEFAULT_DB_PATH = ".ranobelib_companion.db"
_DEFAULT_SESSION_MAX_AGE_SECONDS = 14 * 24 * 60 * 60  # 14 days - Starlette's own default
_DEFAULT_SESSION_REMEMBER_MAX_AGE_SECONDS = 90 * 24 * 60 * 60  # 90 days
_DEFAULT_DOWNLOAD_FILE_TTL_SECONDS = 30 * 60  # 30 minutes
_DEFAULT_AVATAR_DIR = ".ranobelib_avatars"
_DEFAULT_COMMENT_ATTACHMENT_DIR = ".ranobelib_comment_attachments"


@dataclass(frozen=True)
class Settings:
    cache_dir: Path
    cache_ttl: float
    db_path: Path
    session_secret_key: str
    session_max_age: int
    session_remember_max_age: int
    download_file_ttl: float
    avatar_dir: Path
    comment_attachment_dir: Path
    is_production: bool


@lru_cache
def get_settings() -> Settings:
    is_production = os.environ.get("ENVIRONMENT") == "production"
    session_secret_key = os.environ.get("SESSION_SECRET_KEY")
    if not session_secret_key:
        if is_production:
            raise RuntimeError(
                "SESSION_SECRET_KEY is required when ENVIRONMENT=production - without "
                "it, every deploy/restart would silently log everyone out."
            )
        session_secret_key = secrets.token_hex(32)
        logger.warning(
            "SESSION_SECRET_KEY not set - using a random per-process key, which logs "
            "everyone out on every restart. Set it explicitly in production."
        )
    return Settings(
        is_production=is_production,
        cache_dir=Path(os.environ.get("CACHE_DIR", _DEFAULT_CACHE_DIR)),
        cache_ttl=float(os.environ.get("CACHE_TTL_SECONDS", _DEFAULT_CACHE_TTL_SECONDS)),
        db_path=Path(os.environ.get("DB_PATH", _DEFAULT_DB_PATH)),
        session_secret_key=session_secret_key,
        session_max_age=int(
            os.environ.get("SESSION_MAX_AGE_SECONDS", _DEFAULT_SESSION_MAX_AGE_SECONDS)
        ),
        session_remember_max_age=int(
            os.environ.get(
                "SESSION_REMEMBER_MAX_AGE_SECONDS", _DEFAULT_SESSION_REMEMBER_MAX_AGE_SECONDS
            )
        ),
        download_file_ttl=float(
            os.environ.get("DOWNLOAD_FILE_TTL_SECONDS", _DEFAULT_DOWNLOAD_FILE_TTL_SECONDS)
        ),
        avatar_dir=Path(os.environ.get("AVATAR_DIR", _DEFAULT_AVATAR_DIR)),
        comment_attachment_dir=Path(
            os.environ.get("COMMENT_ATTACHMENT_DIR", _DEFAULT_COMMENT_ATTACHMENT_DIR)
        ),
    )

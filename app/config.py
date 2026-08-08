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
deployment, so an unset value is logged as a warning rather than passed silently.
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


@dataclass(frozen=True)
class Settings:
    cache_dir: Path
    cache_ttl: float
    db_path: Path
    session_secret_key: str


@lru_cache
def get_settings() -> Settings:
    session_secret_key = os.environ.get("SESSION_SECRET_KEY")
    if not session_secret_key:
        session_secret_key = secrets.token_hex(32)
        logger.warning(
            "SESSION_SECRET_KEY not set - using a random per-process key, which logs "
            "everyone out on every restart. Set it explicitly in production."
        )
    return Settings(
        cache_dir=Path(os.environ.get("CACHE_DIR", _DEFAULT_CACHE_DIR)),
        cache_ttl=float(os.environ.get("CACHE_TTL_SECONDS", _DEFAULT_CACHE_TTL_SECONDS)),
        db_path=Path(os.environ.get("DB_PATH", _DEFAULT_DB_PATH)),
        session_secret_key=session_secret_key,
    )

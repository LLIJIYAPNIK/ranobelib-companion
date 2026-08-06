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
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DEFAULT_CACHE_DIR = ".ranobelib_cache"
_DEFAULT_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours


@dataclass(frozen=True)
class Settings:
    cache_dir: Path
    cache_ttl: float


@lru_cache
def get_settings() -> Settings:
    return Settings(
        cache_dir=Path(os.environ.get("CACHE_DIR", _DEFAULT_CACHE_DIR)),
        cache_ttl=float(os.environ.get("CACHE_TTL_SECONDS", _DEFAULT_CACHE_TTL_SECONDS)),
    )

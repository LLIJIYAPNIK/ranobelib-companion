"""The only place in this application allowed to construct ``RanobeLib(...)``."""

from ranobelib import RanobeLib

from app.config import get_settings


def get_client(url: str) -> RanobeLib:
    """Build a `RanobeLib` client for a title URL or `{id}--{slug}` identifier."""
    settings = get_settings()
    return RanobeLib(url, cache_dir=settings.cache_dir, cache_ttl=settings.cache_ttl)

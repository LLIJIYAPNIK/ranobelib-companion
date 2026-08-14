"""The only place in this application allowed to construct ``Catalog(...)``."""

from ranobelib import Catalog
from ranobelib.models import Country, Genre

from app.config import get_settings


def get_catalog() -> Catalog:
    """Build a `Catalog` client for listing/searching the ranobelib.me catalog.

    Shares the same `cache_dir`/`cache_ttl` as `get_client()` (app/services/client.py) -
    one common on-disk cache for the whole app, per CLAUDE.md, not a separate one for
    catalog browsing.
    """
    settings = get_settings()
    return Catalog(cache_dir=settings.cache_dir, cache_ttl=settings.cache_ttl)


async def list_genres() -> list[Genre]:
    """Every genre `Catalog.list_titles(genres=[...])` can filter by (PR 38's catalog
    genre-filter checkboxes), sourced from `Catalog.list_genres()` (SDK >=0.7.0) rather
    than a hardcoded id -> name table on the web layer, per CLAUDE.md's Golden rule.
    """
    async with get_catalog() as catalog:
        return await catalog.list_genres()


async def list_countries() -> list[Country]:
    """Every country `Catalog.list_titles(countries=[...])` can filter by (PR 85's
    "Страна" filter section, checkboxes since PR 100), sourced from
    `Catalog.list_countries()` (SDK >=0.8.0) rather than a hardcoded id -> name table on
    the web layer, same reasoning as `list_genres()`.
    """
    async with get_catalog() as catalog:
        return await catalog.list_countries()

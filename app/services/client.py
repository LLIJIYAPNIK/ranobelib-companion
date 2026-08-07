"""The only place in this application allowed to construct ``RanobeLib(...)``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ranobelib import RanobeLib, TitleNotFoundError

from app.config import get_settings


def get_client(url: str) -> RanobeLib:
    """Build a `RanobeLib` client for a title URL or `{id}--{slug}` identifier.

    Raises the SDK's own `ValueError` (not a `RanobeLibError`) if `url` doesn't contain a
    recognizable `{id}--{slug}` segment at all - callers taking raw user-typed/pasted
    input (see `open_title` in app/api/titles.py) want to handle that themselves as a
    distinct "couldn't parse this" case, with the original text shown back to the user.
    Everywhere else, use `open_client()` instead.
    """
    settings = get_settings()
    return RanobeLib(url, cache_dir=settings.cache_dir, cache_ttl=settings.cache_ttl)


@asynccontextmanager
async def open_client(slug_url: str) -> AsyncIterator[RanobeLib]:
    """`get_client(slug_url)` as an async context manager, for routes whose `slug_url`
    comes from a URL path segment rather than raw user-typed text.

    A stale bookmark or hand-edited URL that fails to parse is, from the user's point of
    view, exactly the same situation as a well-formed slug_url the API doesn't recognize -
    so it's surfaced as `TitleNotFoundError` (handled uniformly by the central
    RanobeLibError -> HTTP mapping) rather than leaking the SDK's `ValueError` as an
    unhandled 500.
    """
    try:
        client = get_client(slug_url)
    except ValueError as exc:
        raise TitleNotFoundError(slug_url) from exc
    async with client as lib:
        yield lib

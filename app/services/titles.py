"""Display name and cover for a set of titles, for pages that list titles by slug_url."""

from dataclasses import dataclass

import httpx
from ranobelib import RanobeLibError
from ranobelib.models import Volume

from app.services.client import open_client


@dataclass(frozen=True)
class TitleSummary:
    name: str
    cover_url: str | None


async def title_summaries(slugs: set[str]) -> dict[str, TitleSummary]:
    """Name and cover per slug_url, through the SDK the same "cheap - cache_dir makes it
    a local cache hit after the first request" way library_items_for_user()
    (app/api/library.py) does, rather than stored in this app's own DB (see that
    function's docstring on why not).

    A title that's gone or unreachable on ranobelib.me is simply left out - each caller
    picks its own fallback (a profile tooltip shows the slug_url, a download-history card
    promotes the file name), and one bad title never takes the page down with it.
    """
    summaries: dict[str, TitleSummary] = {}
    for slug_url in slugs:
        try:
            async with open_client(slug_url) as lib:
                title = await lib.get_info()
        except RanobeLibError:
            continue
        summaries[slug_url] = TitleSummary(
            name=title.rus_name or title.name,
            cover_url=title.cover.default or title.cover.md or title.cover.thumbnail,
        )
    return summaries


@dataclass(frozen=True)
class TitleDetails:
    name: str
    cover_url: str | None
    status: str | None
    volumes: list[Volume]
    summary: str | None = None


async def title_details(slug_url: str) -> TitleDetails | None:
    """Name, cover, status, description and chapters in the SDK's order, for the admin's
    title page (PR 346) and the home page's book on the go. None when the SDK can't resolve
    the title or ranobelib.me can't be reached - the page then shows what the
    application's own data has."""
    try:
        async with open_client(slug_url) as lib:
            title = await lib.get_info()
            volumes = await lib.get_table_of_contents()
    except (RanobeLibError, httpx.TransportError):
        return None
    return TitleDetails(
        name=title.rus_name or title.name,
        cover_url=title.cover.default or title.cover.md or title.cover.thumbnail,
        status=title.status.label if title.status else None,
        volumes=volumes,
        summary=title.summary,
    )


async def tables_of_contents(slugs: list[str]) -> dict[str, list[Volume]]:
    """Each title's chapters in the SDK's own order (`get_table_of_contents()`), for the
    admin analytics' reading positions (PR 346). Like title_summaries(), a title the SDK
    can't resolve is left out, and so is one ranobelib.me couldn't be reached for - the
    caller shows "no data" for it rather than failing the page."""
    contents: dict[str, list[Volume]] = {}
    for slug_url in slugs:
        try:
            async with open_client(slug_url) as lib:
                contents[slug_url] = await lib.get_table_of_contents()
        except (RanobeLibError, httpx.TransportError):
            continue
    return contents

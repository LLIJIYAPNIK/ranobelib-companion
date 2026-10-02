"""Display name and cover for a set of titles, for pages that list titles by slug_url."""

from dataclasses import dataclass

from ranobelib import RanobeLibError

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

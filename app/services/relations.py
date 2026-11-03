"""A title's related releases (PR 360): its sequels and spin-offs on ranobelib.me, its manga
and its anime - what ranobelib.me's own title page lists, from `RanobeLib.get_relations()`
(SDK >=1.0.0). The only place in this application that calls it.

Relations cross the whole lib.social network: a novel links to its manga on mangalib.me or
slashlib.me and to its anime (seasons, OVA, specials) on anilib.me. Only the novels on
ranobelib.me itself open inside this application; the rest are links out to their own site.
The relation's wording ("Продолжение", "Адаптация", ...) and the anime's format ("TV Сериал",
"OVA", ...) are the API's own labels, shown as they come.
"""

from dataclasses import dataclass, field

from ranobelib import RanobeLib, RelatedMedia, Relation

from app.services.client import open_client

# TODO(libsocial-sdk#132): the related title's page URL belongs in the SDK
# (https://github.com/LLIJIYAPNIK/libsocial-sdk/issues/132) - until it ships, the page of a
# related title on another lib.social site is built here from its site id. A site missing
# from this table gets a card without a link rather than a guessed URL.
_EXTERNAL_PAGES = {
    1: ("mangalib.me", "https://mangalib.me/ru/manga/"),
    2: ("slashlib.me", "https://slashlib.me/ru/manga/"),
    5: ("anilib.me", "https://anilib.me/ru/anime/"),
}


@dataclass(frozen=True)
class RelatedRelease:
    name: str
    relation: str
    """How it relates to this title, from its own side: «Продолжение», «Адаптация», ..."""
    format: str | None
    """The anime's format (TV Сериал, OVA, ...) or a readable title's type, raw from the API."""
    status: str | None
    cover_url: str | None
    href: str | None
    """`/titles/<slug_url>` for a novel on ranobelib.me, its own site's page otherwise."""
    site: str | None
    """The other site's domain; None for a title that opens inside this application, "" for
    a site this application has no page URL for (then `href` is None too)."""

    @property
    def external(self) -> bool:
        return self.site is not None


@dataclass(frozen=True)
class TitleRelations:
    novels: list[RelatedRelease] = field(default_factory=list)
    manga: list[RelatedRelease] = field(default_factory=list)
    anime: list[RelatedRelease] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.novels or self.manga or self.anime)


async def title_relations(slug_url: str) -> TitleRelations:
    """The title's related releases, grouped into novels / manga / anime, each group in the
    order ranobelib.me's title page shows them."""
    async with open_client(slug_url) as lib:
        relations = await lib.get_relations()
    return group_relations(relations)


def group_relations(relations: list[Relation]) -> TitleRelations:
    grouped = TitleRelations()
    for relation in relations:
        media = relation.media
        release = _release(relation)
        if media.site_id == RanobeLib.site.id:
            grouped.novels.append(release)
        elif media.kind == "anime":
            grouped.anime.append(release)
        else:
            grouped.manga.append(release)
    return grouped


def _release(relation: Relation) -> RelatedRelease:
    media = relation.media
    if media.site_id == RanobeLib.site.id:
        site, href = None, f"/titles/{media.slug_url}"
    elif media.site_id in _EXTERNAL_PAGES:
        site, base = _EXTERNAL_PAGES[media.site_id]
        href = base + media.slug_url
    else:
        site, href = "", None
    return RelatedRelease(
        name=_display_name(media),
        relation=relation.type.label,
        format=media.type.label if media.type else None,
        status=media.status.label if media.status else None,
        cover_url=media.cover.default or media.cover.md or media.cover.thumbnail,
        href=href,
        site=site,
    )


def _display_name(media: RelatedMedia) -> str:
    return media.rus_name or media.eng_name or media.name

"""PR 295: the catalog feed's featured rhythm - app/services/catalog.py's interleave()
(the design's `buildStream`, resumable across pages) and catalog_stream()."""

from ranobelib import CatalogPage
from ranobelib.models import Cover, Label, Title

from app.services.catalog import FEATURED_EVERY, catalog_stream, interleave


def _title(id_: int) -> Title:
    return Title(
        id=id_,
        name=f"Novel {id_}",
        slug=f"novel-{id_}",
        slug_url=f"{id_}--novel-{id_}",
        cover=Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
    )


def _layout(items: list) -> list[str]:
    return [f"{item.kind[0].upper()}{item.title.id}" for item in items]


def test_one_featured_after_every_twelve_cards() -> None:
    regular = [_title(i) for i in range(1, 31)]
    top = [_title(i) for i in range(1001, 1004)]

    items, shown, featured = interleave(regular, top, shown=0, featured=0)

    kinds = [item.kind for item in items]
    assert kinds.count("card") == 30
    assert [i for i, kind in enumerate(kinds) if kind == "featured"] == [12, 25]
    assert [item.title.id for item in items if item.kind == "featured"] == [1001, 1002]
    assert [item.issue for item in items if item.kind == "featured"] == [1, 2]
    assert (shown, featured) == (30, 2)


def test_rhythm_counts_over_the_whole_feed_not_per_page() -> None:
    """Pages of 20: the inserts land after the 12th, 24th, 36th... card of the feed."""
    top = [_title(i) for i in range(1001, 1010)]
    first, shown, featured = interleave(
        [_title(i) for i in range(1, 21)], top, shown=0, featured=0
    )
    second, shown, featured = interleave(
        [_title(i) for i in range(21, 41)], top, shown=shown, featured=featured
    )

    feed = first + second
    positions = [i for i, item in enumerate(feed) if item.kind == "featured"]
    cards_before = [sum(1 for x in feed[:p] if x.kind == "card") for p in positions]
    assert cards_before == [12, 24, 36]
    assert [item.issue for item in feed if item.kind == "featured"] == [1, 2, 3]
    assert (shown, featured) == (40, 3)


def test_no_insert_once_the_featured_source_runs_out() -> None:
    items, shown, featured = interleave(
        [_title(i) for i in range(1, 31)], [_title(1001)], shown=0, featured=0
    )
    assert [item.kind for item in items].count("featured") == 1
    assert (shown, featured) == (30, 1)


class _Catalog:
    def __init__(self, regular: list[Title], views_pages: list[list[Title]]) -> None:
        self._regular = regular
        self._views = views_pages
        self.calls: list[dict[str, object]] = []

    async def list_titles(self, **kwargs: object) -> CatalogPage:
        self.calls.append(kwargs)
        if kwargs["sort"] == "views":
            index = int(kwargs["page"]) - 1  # type: ignore[call-overload]
            return CatalogPage(
                items=self._views[index], page=index + 1, has_next_page=index + 1 < len(self._views)
            )
        return CatalogPage(items=self._regular, page=1, has_next_page=True)


async def test_results_mode_is_the_plain_listing_without_inserts() -> None:
    catalog = _Catalog([_title(i) for i in range(1, 31)], [[_title(1001)]])

    stream = await catalog_stream(
        catalog,  # type: ignore[arg-type]
        editorial=False,
        page=1,
        shown=0,
        featured=0,
        query="dxd",
        sort="last_chapter_at",
        genres=[],
        countries=[],
        tags=[],
    )

    assert all(item.kind == "card" for item in stream.items)
    assert all(call["sort"] != "views" for call in catalog.calls)
    assert (stream.shown, stream.featured) == (0, 0)


async def test_editorial_reads_more_views_pages_only_when_inserts_need_them() -> None:
    views = [[_title(1001)], [_title(1002)], [_title(1003)]]
    catalog = _Catalog([_title(i) for i in range(1, 2 * FEATURED_EVERY + 1)], views)

    stream = await catalog_stream(
        catalog,  # type: ignore[arg-type]
        editorial=True,
        page=1,
        shown=0,
        featured=0,
        query=None,
        sort="last_chapter_at",
        genres=[],
        countries=[],
        tags=[],
    )

    assert [call["page"] for call in catalog.calls if call["sort"] == "views"] == [1, 2]
    assert [item.title.id for item in stream.items if item.kind == "featured"] == [1001, 1002]
    assert stream.has_next_page is True
    assert (stream.shown, stream.featured) == (24, 2)


# --- dedup: a featured title is never also a regular card -----------------------------


def test_titles_of_the_featured_buffer_are_dropped_from_the_regular_cards() -> None:
    # 7 and 20 are top by views too - each is shown only as a featured insert.
    regular = [_title(i) for i in range(1, 31)]
    top = [_title(7), _title(20), _title(1001)]

    items, shown, featured = interleave(regular, top, shown=0, featured=0)

    cards = [item.title.id for item in items if item.kind == "card"]
    assert 7 not in cards and 20 not in cards
    assert len(cards) == 28 and shown == 28
    ids = [item.title.id for item in items]
    assert ids.count(7) == 1 and ids.count(20) == 1


def test_a_buffered_title_is_not_a_card_even_before_its_insert_is_due() -> None:
    """The title next to its own insert - the case the design rules out by name."""
    regular = [_title(i) for i in range(1, 13)]  # card 12 would sit right before insert 1
    items, _, _ = interleave(regular, [_title(12)], shown=0, featured=0)

    assert _layout(items) == [f"C{i}" for i in range(1, 12)]


async def test_dedup_holds_on_a_later_page_too() -> None:
    views = [[_title(5), _title(40), _title(1001)]]
    catalog = _Catalog([_title(i) for i in range(31, 61)], views)

    stream = await catalog_stream(
        catalog,  # type: ignore[arg-type]
        editorial=True,
        page=2,
        shown=30,
        featured=1,
        query=None,
        sort="last_chapter_at",
        genres=[],
        countries=[],
        tags=[],
    )

    cards = [item.title.id for item in stream.items if item.kind == "card"]
    assert 40 not in cards
    assert [item.title.id for item in stream.items if item.kind == "featured"] == [40, 1001]

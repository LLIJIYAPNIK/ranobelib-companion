"""PR 360: a title's related releases from `RanobeLib.get_relations()`, grouped into novels on
ranobelib.me (opened inside the app), manga and anime (links out to their own site)."""

import logging
from contextlib import asynccontextmanager

import httpx
import pytest
from ranobelib import AccessBlockedError, RateLimitError, Relation, TitleNotFoundError

from app.services import relations
from app.services.relations import group_relations, title_relations


def _relation(
    label: str,
    *,
    site: int,
    model: str = "manga",
    slug_url: str = "1--x",
    name: str = "X",
    rus_name: str | None = None,
    eng_name: str | None = None,
    type_label: str | None = None,
    cover: str | None = None,
) -> Relation:
    return Relation.model_validate(
        {
            "related_type": {"id": 1, "label": label},
            "media": {
                "id": 1,
                "name": name,
                "rus_name": rus_name,
                "eng_name": eng_name,
                "slug": slug_url.split("--", 1)[-1],
                "slug_url": slug_url,
                "model": model,
                "site": site,
                "cover": {"default": cover} if cover else {},
                "type": {"id": 1, "label": type_label} if type_label else None,
                "status": {"id": 2, "label": "Завершён"},
            },
        }
    )


def test_novels_manga_and_anime_go_to_their_own_groups_in_the_api_order() -> None:
    grouped = group_relations(
        [
            _relation(
                "Адаптация", site=5, model="anime", slug_url="10--tv", type_label="TV Сериал"
            ),
            _relation("Продолжение", site=3, slug_url="2--sequel"),
            _relation("Адаптация", site=2, slug_url="3--manga"),
            _relation("Адаптация", site=5, model="anime", slug_url="11--ova", type_label="OVA"),
            _relation("Спин-офф", site=3, slug_url="4--spin"),
            _relation("Адаптация", site=1, slug_url="5--manga"),
        ]
    )

    assert [r.href for r in grouped.novels] == ["/titles/2--sequel", "/titles/4--spin"]
    assert [r.relation for r in grouped.novels] == ["Продолжение", "Спин-офф"]
    assert [r.href for r in grouped.manga] == [
        "https://slashlib.me/ru/manga/3--manga",
        "https://mangalib.me/ru/manga/5--manga",
    ]
    assert [(r.format, r.href) for r in grouped.anime] == [
        ("TV Сериал", "https://anilib.me/ru/anime/10--tv"),
        ("OVA", "https://anilib.me/ru/anime/11--ova"),
    ]
    assert grouped


def test_a_novel_on_ranobelib_opens_inside_the_app_and_others_are_external() -> None:
    grouped = group_relations(
        [_relation("Продолжение", site=3), _relation("Адаптация", site=5, model="anime")]
    )

    assert not grouped.novels[0].external and grouped.novels[0].site is None
    assert grouped.anime[0].external and grouped.anime[0].site == "anilib.me"


def test_an_unknown_site_gets_a_card_without_a_guessed_link() -> None:
    (release,) = group_relations([_relation("Адаптация", site=42)]).manga

    assert release.href is None and release.external


def test_the_russian_name_first_then_english_then_original() -> None:
    grouped = group_relations(
        [
            _relation("a", site=3, name="Orig", rus_name="Рус", eng_name="Eng"),
            _relation("b", site=3, name="Orig", eng_name="Eng"),
            _relation("c", site=3, name="Orig"),
        ]
    )

    assert [r.name for r in grouped.novels] == ["Рус", "Eng", "Orig"]


def test_cover_status_and_a_missing_format() -> None:
    (release,) = group_relations(
        [_relation("a", site=1, cover="https://cover.imglib.info/c.jpg")]
    ).manga

    assert release.cover_url == "https://cover.imglib.info/c.jpg"
    assert release.status == "Завершён"
    assert release.format is None


def test_no_relations_is_falsy() -> None:
    assert not group_relations([])


async def test_title_relations_reads_the_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []

    class _Lib:
        async def get_relations(self) -> list[Relation]:
            return [_relation("Продолжение", site=3, slug_url="2--next")]

    @asynccontextmanager
    async def fake_open(slug_url: str):
        opened.append(slug_url)
        yield _Lib()

    monkeypatch.setattr(relations, "open_client", fake_open)

    result = await title_relations("1--title")

    assert opened == ["1--title"]
    assert [r.href for r in result.novels] == ["/titles/2--next"]


@pytest.mark.parametrize(
    "error",
    [
        TitleNotFoundError("1--title"),
        RateLimitError(),
        AccessBlockedError("https://api.lib.social/api/manga/1--title"),
        httpx.ConnectError("down"),
    ],
)
async def test_any_sdk_or_network_error_means_no_block_not_a_failed_page(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    class _Lib:
        async def get_relations(self) -> list[Relation]:
            raise error

    @asynccontextmanager
    async def fake_open(slug_url: str):
        yield _Lib()

    monkeypatch.setattr(relations, "open_client", fake_open)

    with caplog.at_level(logging.WARNING, logger="app.services.relations"):
        result = await title_relations("1--title")

    assert not result
    assert "1--title" in caplog.text

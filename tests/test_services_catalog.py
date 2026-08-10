from unittest.mock import patch

from ranobelib.models import Genre

from app.config import get_settings
from app.services.catalog import get_catalog, list_genres


def test_get_catalog_constructs_catalog_with_configured_cache() -> None:
    settings = get_settings()

    with patch("app.services.catalog.Catalog") as mock_catalog:
        get_catalog()

    mock_catalog.assert_called_once_with(
        cache_dir=settings.cache_dir, cache_ttl=settings.cache_ttl
    )


class _FakeCatalog:
    def __init__(self, genres: list[Genre]) -> None:
        self._genres = genres

    async def __aenter__(self) -> "_FakeCatalog":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def list_genres(self) -> list[Genre]:
        return self._genres


async def test_list_genres_delegates_to_the_catalog_client() -> None:
    genres = [Genre(id=5, name="Фэнтези"), Genre(id=8, name="Романтика")]
    with patch("app.services.catalog.get_catalog", return_value=_FakeCatalog(genres)):
        result = await list_genres()

    assert result == genres

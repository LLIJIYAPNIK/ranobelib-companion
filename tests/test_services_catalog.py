from unittest.mock import patch

from app.config import get_settings
from app.services.catalog import get_catalog


def test_get_catalog_constructs_catalog_with_configured_cache() -> None:
    settings = get_settings()

    with patch("app.services.catalog.Catalog") as mock_catalog:
        get_catalog()

    mock_catalog.assert_called_once_with(
        cache_dir=settings.cache_dir, cache_ttl=settings.cache_ttl
    )

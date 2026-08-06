from unittest.mock import patch

from app.config import get_settings
from app.services.client import get_client


def test_get_client_constructs_ranobelib_with_configured_cache() -> None:
    url = "https://ranobelib.me/ru/book/6712--high-school-dxd-novel"
    settings = get_settings()

    with patch("app.services.client.RanobeLib") as mock_ranobelib:
        get_client(url)

    mock_ranobelib.assert_called_once_with(
        url, cache_dir=settings.cache_dir, cache_ttl=settings.cache_ttl
    )

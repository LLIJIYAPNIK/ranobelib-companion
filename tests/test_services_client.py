from unittest.mock import patch

import pytest
from ranobelib import TitleNotFoundError

from app.config import get_settings
from app.services.client import get_client, open_client


def test_get_client_constructs_ranobelib_with_configured_cache() -> None:
    url = "https://ranobelib.me/ru/book/6712--high-school-dxd-novel"
    settings = get_settings()

    with patch("app.services.client.RanobeLib") as mock_ranobelib:
        get_client(url)

    mock_ranobelib.assert_called_once_with(
        url, cache_dir=settings.cache_dir, cache_ttl=settings.cache_ttl
    )


async def test_open_client_converts_unparseable_slug_to_title_not_found() -> None:
    with pytest.raises(TitleNotFoundError):
        async with open_client("not-a-valid-slug"):
            pass


async def test_open_client_yields_the_client_for_a_valid_slug() -> None:
    class _FakeClient:
        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

    fake = _FakeClient()
    with patch("app.services.client.RanobeLib", return_value=fake):
        async with open_client("6712--test-novel") as lib:
            assert lib is fake

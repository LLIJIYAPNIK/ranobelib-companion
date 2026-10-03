"""PR 293: "Избранное" is gone from the app, but library_entries.is_favorite and
users.show_favorite stay in the database as they were - no migration, nothing reads or
writes them any more. Every operation that used to touch them (or now sits where they
were) must leave the stored values alone, so the decision stays reversible.
"""

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib.models import Cover, Label, Title

from app.config import get_settings
from app.db.connection import connection
from app.db.library import record_progress
from tests.auth_helpers import register
from tests.db_reset import reset_app_database


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def _fake_title(slug_url: str) -> Title:
    return Title(
        id=int(slug_url.split("--")[0]),
        name="Test Novel",
        slug=slug_url.split("--")[1],
        slug_url=slug_url,
        cover=Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
    )


class _FakeClient:
    def __init__(self, title: Title) -> None:
        self._title = title

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_info(self) -> Title:
        return self._title

    async def get_table_of_contents(self) -> list:
        return []

    async def estimate_title_size(self) -> int:
        return 0


async def _stored_flags() -> tuple[dict[str, int], int]:
    async with connection() as conn:
        cursor = await conn.execute("SELECT slug_url, is_favorite FROM library_entries")
        entries = {row["slug_url"]: row["is_favorite"] for row in await cursor.fetchall()}
        cursor = await conn.execute(
            "SELECT show_favorite FROM users WHERE email = %s", ("alice@example.com",)
        )
        show_favorite = (await cursor.fetchone())["show_favorite"]
    return entries, show_favorite


@pytest.mark.parametrize("stored_show_favorite", [0, 1])
async def test_favorite_columns_survive_every_remaining_operation(
    client: TestClient, stored_show_favorite: int
) -> None:
    register(client, "alice@example.com")
    for slug_url in ("1--first", "2--second", "3--third"):
        with patch(
            "app.services.client.RanobeLib", return_value=_FakeClient(_fake_title(slug_url))
        ):
            client.post(f"/library/{slug_url}/add")
    async with connection() as conn:
        await conn.execute(
            "UPDATE library_entries SET is_favorite = 1 WHERE slug_url = %s", ("1--first",)
        )
        await conn.execute(
            "UPDATE users SET show_favorite = %s WHERE email = %s",
            (stored_show_favorite, "alice@example.com"),
        )

    title = _fake_title("1--first")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        assert client.get("/library").status_code == 200
        assert client.get("/profile").status_code == 200
    for old_url in ("/library?tab=favorites", "/library?tab=fav", "/library/favorites"):
        assert client.get(old_url, follow_redirects=False).status_code == 301
    assert client.post("/library/2--second/favorite").status_code == 404
    # Every privacy switch both ways, from both places that save them.
    client.post("/settings/account/privacy", data={})
    client.post(
        "/settings/account/privacy",
        data={
            "show_currently_reading": "on",
            "show_library": "on",
            "show_friends": "on",
            "show_friends_activity_home": "on",
            "show_favorite": "on",  # a stale form still sending it is ignored
        },
    )
    client.post("/friends/privacy", data={"show_currently_reading": "false"})
    client.post("/library/1--first/default-translation", data={"translation_index": "1"})
    async with connection() as conn:
        await record_progress(conn, 1, "1--first", "1", "3")
    client.post("/library/3--third/remove")

    entries, show_favorite = await _stored_flags()
    assert entries == {"1--first": 1, "2--second": 0}
    assert show_favorite == stored_show_favorite

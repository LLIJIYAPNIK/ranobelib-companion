import json
from unittest.mock import patch
from urllib.parse import quote, unquote

from fastapi.testclient import TestClient
from ranobelib.models import Cover, Label, Title, Volume

from app.main import app

client = TestClient(app)


def _fake_title(slug_url: str, name: str, cover: Cover | None = None) -> Title:
    return Title(
        id=1,
        name=name,
        slug=slug_url.split("--", 1)[1],
        slug_url=slug_url,
        cover=cover or Cover(),
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

    async def get_table_of_contents(self) -> list[Volume]:
        return []

    async def estimate_title_size(self) -> int:
        return 0


def test_show_title_sets_recent_titles_cookie() -> None:
    client.cookies.clear()
    title = _fake_title("1--first-novel", "First Novel")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/1--first-novel")

    cookie = response.cookies.get("recent_titles")
    assert cookie is not None
    assert json.loads(unquote(cookie)) == [
        {"slug_url": "1--first-novel", "name": "First Novel", "cover_url": None}
    ]


def test_show_title_stores_cover_url_in_recent_titles_cookie() -> None:
    client.cookies.clear()
    title = _fake_title(
        "1--first-novel", "First Novel", cover=Cover(default="https://example.com/cover.jpg")
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/1--first-novel")

    cookie = json.loads(unquote(response.cookies.get("recent_titles")))
    assert cookie[0]["cover_url"] == "https://example.com/cover.jpg"


def test_show_title_moves_reopened_title_to_front() -> None:
    client.cookies.clear()
    first = _fake_title("1--first-novel", "First Novel")
    second = _fake_title("2--second-novel", "Second Novel")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(first)):
        client.get("/titles/1--first-novel")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(second)):
        client.get("/titles/2--second-novel")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(first)):
        response = client.get("/titles/1--first-novel")

    cookie = json.loads(unquote(response.cookies.get("recent_titles")))
    assert [item["slug_url"] for item in cookie] == ["1--first-novel", "2--second-novel"]


def test_home_lists_recent_titles_from_cookie() -> None:
    client.cookies.clear()
    client.cookies.set(
        "recent_titles",
        quote(json.dumps([{"slug_url": "1--first-novel", "name": "First Novel"}])),
    )

    response = client.get("/")

    assert "First Novel" in response.text
    assert 'href="/titles/1--first-novel"' in response.text
    assert "<img" not in response.text  # old cookie shape, no cover_url - no crash either

    client.cookies.clear()


def test_home_renders_cover_when_present_in_cookie() -> None:
    client.cookies.clear()
    client.cookies.set(
        "recent_titles",
        quote(
            json.dumps(
                [
                    {
                        "slug_url": "1--first-novel",
                        "name": "First Novel",
                        "cover_url": "https://example.com/cover.jpg",
                    }
                ]
            )
        ),
    )

    response = client.get("/")

    assert 'src="https://example.com/cover.jpg"' in response.text

    client.cookies.clear()


def test_home_ignores_malformed_recent_titles_cookie() -> None:
    client.cookies.clear()
    client.cookies.set("recent_titles", "not-json")

    response = client.get("/")

    assert response.status_code == 200

    client.cookies.clear()

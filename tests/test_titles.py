from unittest.mock import patch

from fastapi.testclient import TestClient
from ranobelib import RanobeLibError, RateLimitError, TitleNotFoundError
from ranobelib.models import Chapter, Cover, Label, Tag, Title, Volume

from app.main import app

client = TestClient(app, follow_redirects=False)


def _fake_title(slug_url: str = "6712--test-novel") -> Title:
    return Title(
        id=6712,
        name="Test Novel",
        rus_name="Тестовый роман",
        slug="test-novel",
        slug_url=slug_url,
        cover=Cover(default="https://example.com/cover.jpg"),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
        chapter_count=42,
    )


class _FakeClient:
    def __init__(self, title: Title, volumes: list[Volume] | None = None) -> None:
        self._title = title
        self._volumes = volumes or []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_info(self) -> Title:
        return self._title

    async def get_table_of_contents(self) -> list[Volume]:
        return self._volumes


class _RaisingClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def __aenter__(self) -> "_RaisingClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_info(self) -> Title:
        raise self._exc


def test_open_title_redirects_to_canonical_slug_url() -> None:
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get(
            "/titles/open",
            params={"url": "https://ranobelib.me/ru/book/6712--test-novel"},
        )

    assert response.status_code == 302
    assert response.headers["location"] == "/titles/6712--test-novel"


def test_open_title_rejects_unparseable_input() -> None:
    response = client.get("/titles/open", params={"url": "not a link at all"})

    assert response.status_code == 400
    assert "Не удалось распознать ссылку" in response.text


def test_open_title_rate_limited() -> None:
    exc = RateLimitError(retry_after=30)
    with patch("app.services.client.RanobeLib", return_value=_RaisingClient(exc)):
        response = client.get(
            "/titles/open",
            params={"url": "https://ranobelib.me/ru/book/6712--test-novel"},
        )

    assert response.status_code == 429
    assert response.json() == {
        "detail": "ranobelib сейчас ограничивает запросы, попробуйте позже"
    }


def test_show_title_renders_metadata() -> None:
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/6712--test-novel")

    assert response.status_code == 200
    assert "Test Novel" in response.text
    assert "Тестовый роман" in response.text
    assert "https://example.com/cover.jpg" in response.text
    assert "42" in response.text


def test_show_title_uses_russian_name_as_the_primary_heading() -> None:
    # PR 25: rus_name (already in the SDK's Title model, no issue needed) becomes the
    # primary display name wherever the title's name is shown, with the original name
    # falling back to an alt-name line instead of disappearing.
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/6712--test-novel")

    assert response.status_code == 200
    assert "<title>Тестовый роман — RanobeLib Companion</title>" in response.text
    assert '<h1 class="title-hero__name">Тестовый роман</h1>' in response.text
    assert '<p class="title-hero__alt-name">Test Novel</p>' in response.text
    assert 'alt="Обложка «Тестовый роман»"' in response.text


def test_show_title_falls_back_to_original_name_without_a_russian_one() -> None:
    title = Title(
        id=6712,
        name="Test Novel",
        slug="test-novel",
        slug_url="6712--test-novel",
        cover=Cover(default="https://example.com/cover.jpg"),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/6712--test-novel")

    assert response.status_code == 200
    assert "<title>Test Novel — RanobeLib Companion</title>" in response.text
    assert '<h1 class="title-hero__name">Test Novel</h1>' in response.text
    assert "title-hero__alt-name" not in response.text


def test_show_title_renders_full_metadata() -> None:
    title = Title(
        id=6712,
        name="Test Novel",
        rus_name="Тестовый роман",
        eng_name="Test Novel EN",
        other_names=["Alt Title"],
        slug="test-novel",
        slug_url="6712--test-novel",
        cover=Cover(default="https://example.com/cover.jpg"),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
        release_date="2020",
        tags=[Tag(id=1, name="Реинкарнация")],
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/6712--test-novel")

    assert response.status_code == 200
    assert "Test Novel EN" in response.text
    assert "Alt Title" in response.text
    assert "2020" in response.text
    assert "Реинкарнация" in response.text


def test_show_title_renders_table_of_contents() -> None:
    title = _fake_title()
    volumes = [
        Volume(
            number="1",
            chapters=[
                Chapter(id=1, volume="1", number="1", name="Начало"),
                Chapter(id=2, volume="1", number="1.5", name=None),
                Chapter(id=3, volume="1", number="2", name="Переводы", branches_count=3),
            ],
        )
    ]
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        response = client.get("/titles/6712--test-novel")

    assert response.status_code == 200
    assert "Том 1" in response.text
    assert "Начало" in response.text
    assert "1.5" in response.text
    assert "Без названия" in response.text
    assert "3 переводов" in response.text
    assert response.text.count("переводов") == 1
    assert 'name="chapters"' in response.text
    assert 'value="1--1"' in response.text
    assert 'value="1--1.5"' in response.text
    assert 'href="/titles/6712--test-novel/chapters/1/1"' in response.text


def test_show_title_renders_export_form() -> None:
    title = _fake_title()
    volumes = [
        Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1", name="Начало")])
    ]
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        response = client.get("/titles/6712--test-novel")

    assert response.status_code == 200
    assert 'action="/titles/6712--test-novel/export"' in response.text
    assert 'name="fmt"' in response.text
    assert '<option value="epub">EPUB</option>' in response.text
    assert '<option value="txt">TXT</option>' in response.text
    assert (
        'formaction="/titles/6712--test-novel/volumes/1/export"' in response.text
    )
    assert "Скачать том" in response.text
    assert 'action="/titles/6712--test-novel/download"' in response.text
    assert "Скачать тайтл" in response.text


def test_show_title_not_found_renders_html_error_page() -> None:
    exc = TitleNotFoundError("6712--missing")
    with patch("app.services.client.RanobeLib", return_value=_RaisingClient(exc)):
        response = client.get("/titles/6712--missing", headers={"accept": "text/html"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "Тайтл не найден, проверьте ссылку" in response.text
    assert 'href="/"' in response.text


def test_show_title_not_found_returns_json_without_html_accept() -> None:
    exc = TitleNotFoundError("6712--missing")
    with patch("app.services.client.RanobeLib", return_value=_RaisingClient(exc)):
        response = client.get("/titles/6712--missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "Тайтл не найден, проверьте ссылку"}


def test_show_title_malformed_slug_url_returns_friendly_404_not_500() -> None:
    # No RanobeLib patch here on purpose - the real SDK class raises a plain ValueError
    # for a slug_url that doesn't parse, which open_client() must convert rather than
    # letting it fall through as an unhandled 500.
    response = client.get("/titles/not-a-valid-slug")

    assert response.status_code == 404
    assert response.json() == {"detail": "Тайтл не найден, проверьте ссылку"}


class _UnmappedError(RanobeLibError):
    """A stand-in for a future RanobeLibError subclass this app's table doesn't cover
    yet - used to confirm the fallback branch's HTML page never leaks the exception's
    own message (which could carry internal detail)."""


def test_show_title_unmapped_error_html_page_hides_the_message() -> None:
    exc = _UnmappedError("some internal SDK detail")
    with patch("app.services.client.RanobeLib", return_value=_RaisingClient(exc)):
        response = client.get("/titles/6712--test-novel", headers={"accept": "text/html"})

    assert response.status_code == 500
    assert "Внутренняя ошибка, попробуйте позже" in response.text
    assert "some internal SDK detail" not in response.text

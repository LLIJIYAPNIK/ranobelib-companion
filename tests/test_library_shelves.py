"""«Полки» (the library page): which shelf each title stands on, and the lines under it."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.api.library import _added_label, _library_context, _shelf_of
from app.book_focus import BookFocus

TODAY = date(2026, 10, 1)


def _entry(slug: str, volume: str | None = None, number: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        slug_url=slug,
        last_read_volume=volume,
        last_read_number=number,
        last_read_at="2026-09-30T20:00:00+00:00" if volume else None,
        added_at="2026-09-20T12:00:00+00:00",
    )


def _focus(position: int | None, total: int) -> BookFocus:
    return BookFocus(summary=None, status="Онгоинг", position=position, total=total, chapters=[])


def test_never_opened_is_not_started_whatever_the_sdk_says() -> None:
    assert _shelf_of(_entry("a"), _focus(None, 64)) == "new"
    assert _shelf_of(_entry("a"), None) == "new"


def test_the_last_chapter_there_is_means_finished() -> None:
    assert _shelf_of(_entry("a", "4", "334"), _focus(334, 334)) == "done"
    # A new chapter came out: back to «Читаю» by itself.
    assert _shelf_of(_entry("a", "4", "334"), _focus(334, 335)) == "reading"


def test_without_the_sdk_an_opened_title_stays_in_reading() -> None:
    assert _shelf_of(_entry("a", "1", "3"), None) == "reading"
    # Its chapter is gone upstream: no place to compare against the end.
    assert _shelf_of(_entry("a", "1", "3"), _focus(None, 50)) == "reading"


def test_focus_counts_the_chapters_left() -> None:
    assert _focus(214, 520).left == 306
    assert _focus(None, 520).left is None


def test_shelves_keep_the_route_order_and_their_own() -> None:
    items = [
        {"entry": _entry("read-1", "1", "5"), "name": "Первый"},
        {"entry": _entry("new-1"), "name": "Новый"},
        {"entry": _entry("done-1", "2", "40"), "name": "Дочитанный"},
        {"entry": _entry("read-2", "1", "2"), "name": "Второй"},
    ]
    focus = {
        "read-1": _focus(5, 100),
        "new-1": _focus(None, 64),
        "done-1": _focus(40, 40),
        "read-2": None,
    }
    context = _library_context(items, focus)

    shelves = {shelf["key"]: shelf for shelf in context["shelves"]}
    assert [shelf["key"] for shelf in context["shelves"]] == ["reading", "new", "done"]
    assert [shelf["title"] for shelf in context["shelves"]] == ["Читаю", "Не начаты", "Дочитаны"]
    assert [i["entry"].slug_url for i in shelves["reading"]["titles"]] == ["read-1", "read-2"]
    assert [i["entry"].slug_url for i in shelves["new"]["titles"]] == ["new-1"]
    assert [i["entry"].slug_url for i in shelves["done"]["titles"]] == ["done-1"]
    assert shelves["reading"]["titles"][0]["focus"] is focus["read-1"]
    assert context["library_count"] == 4


def test_a_guest_gets_three_empty_shelves() -> None:
    context = _library_context([], {})
    assert [len(shelf["titles"]) for shelf in context["shelves"]] == [0, 0, 0]


def test_added_label() -> None:
    assert _added_label("2026-10-01T08:00:00+00:00", TODAY) == "Добавлен сегодня"
    assert _added_label("2026-09-30T08:00:00", TODAY) == "Добавлен вчера"
    assert _added_label("2026-09-25T12:00:00+00:00", TODAY) == "Добавлен 25 сентября"
    assert _added_label("2025-05-02T12:00:00+00:00", TODAY) == "Добавлен 2 мая 2025"

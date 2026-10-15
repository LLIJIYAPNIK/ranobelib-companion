"""PR 350: a chapter's paragraphs as text for the admin comment drawer - the temporary
workaround until ranobelib-python-sdk#77 ships ``chapter_paragraphs()``. The index must
match the reader's (top-level elements of ``Chapter.content``)."""

from contextlib import asynccontextmanager

import httpx
import pytest
from ranobelib import MultipleTranslationsError
from ranobelib.models import Chapter

from app.services import paragraphs
from app.services.paragraphs import paragraph_text, top_level_texts


def test_one_entry_per_top_level_element() -> None:
    content = (
        "<p>Од<b>ин</b> &amp;\n   два</p>\n"
        '<p><img loading="lazy" src="https://x/1.jpg" /></p>'
        "<br>"
        "<blockquote><p>a</p><p>b</p></blockquote>"
        "stray text"  # not an element - the reader doesn't count it either
        "<hr/>"
        "<p>x<br>y</p>"
    )

    assert top_level_texts(content) == ["Один & два", "", "", "a b", "", "x y"]
    assert top_level_texts("") == []


class _Lib:
    def __init__(self, result: Chapter | Exception) -> None:
        self.result = result
        self.calls: list[tuple] = []

    async def get_chapter(self, volume: int, number: str, *, branch_id: int | None = None):
        self.calls.append((volume, number, branch_id))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _use(monkeypatch: pytest.MonkeyPatch, lib: _Lib) -> None:
    @asynccontextmanager
    async def fake_open(slug_url: str):
        yield lib

    monkeypatch.setattr(paragraphs, "open_client", fake_open)


async def test_the_paragraph_by_its_index(monkeypatch: pytest.MonkeyPatch) -> None:
    lib = _Lib(Chapter(id=1, volume="2", number="7", content="<p>Первый</p><p>Второй</p>"))
    _use(monkeypatch, lib)

    assert await paragraph_text("1--t", "2", "7", "15", 1) == "Второй"
    assert await paragraph_text("1--t", "2", "7", "", 0) == "Первый"
    assert await paragraph_text("1--t", "2", "7", "", 2) is None  # the chapter got shorter
    assert lib.calls[:2] == [(2, "7", 15), (2, "7", None)]


@pytest.mark.parametrize(
    "failure",
    [
        MultipleTranslationsError("1--t", volume="2", number="7", branches=[]),
        httpx.ConnectError("unreachable"),
    ],
)
async def test_no_quote_when_the_chapter_cant_be_loaded(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    _use(monkeypatch, _Lib(failure))

    assert await paragraph_text("1--t", "2", "7", "", 0) is None

"""GET / — the search/open-title landing page."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from ranobelib import RanobeLibError

from app.api.activity import ActivitySummary, build_activity_summary
from app.api.library import library_items_for_user
from app.auth.dependencies import get_current_user
from app.db.connection import connection
from app.db.library import get_entry, list_entries
from app.db.users import User
from app.reading_progress import reading_progress_percent
from app.recent_titles import forget, read_recent
from app.services.catalog import popular_titles
from app.services.client import open_client
from app.services.titles import title_details
from app.templating import templates

router = APIRouter()

_POPULAR_LIMIT = 12
# Titles with reading progress the home page carries: the book on the go plus the rest
# of «Читаю».
_READING_LIMIT = 9
# Each book on the shelf: chapters listed from the reader's own on.
_NEXT_CHAPTERS = 4


@dataclass(frozen=True)
class HomeDashboard:
    """The signed-in dashboard sections introduced by PR 273.

    All values come from the existing library and activity stores. The home page only
    composes them; it doesn't create a second progress or activity model.
    """

    reading: list[dict[str, object]]
    summary: ActivitySummary

    @property
    def hero(self) -> dict[str, object] | None:
        return self.reading[0] if self.reading else None


@dataclass(frozen=True)
class BookChapter:
    volume: str
    number: str
    name: str | None
    is_current: bool


@dataclass(frozen=True)
class BookFocus:
    """A book on the shelf, from the SDK: its description, where the reader is in it, and
    the chapters from the current one on, in the SDK's order."""

    summary: str | None
    status: str | None
    position: int | None  # 1-based place of the current chapter, None if not found
    total: int
    chapters: list[BookChapter]


@router.get("/")
async def home(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)],
) -> HTMLResponse:
    dashboard = await _home_dashboard(user)
    hero = dashboard.hero if dashboard else None
    # «Популярное» is the way in for a guest and for a reader with nothing read yet.
    popular = await popular_titles(_POPULAR_LIMIT) if hero is None else []
    # «Витрина» shows any book on the shelf in depth once it's brought forward, so each
    # gets its description and chapters ahead - one SDK lookup per book, in turn (no
    # parallel requests past the SDK's own pacing), cached by the SDK after the first.
    focus = {
        item["entry"].slug_url: await _book_focus(item)  # type: ignore[attr-defined]
        for item in (dashboard.reading if dashboard else [])
    }
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "active_nav": "home",
            "focus": focus,
            "dashboard": dashboard,
            # The library home shows what's being read; «Недавние» are for the rest.
            "recent": await _recent_with_progress(request, user) if hero is None else [],
            "popular": [
                {
                    "slug_url": title.slug_url,
                    "name": title.rus_name or title.name,
                    "cover_url": title.cover.default or title.cover.md or title.cover.thumbnail,
                }
                for title in popular
            ],
        },
    )


async def _book_focus(item: dict[str, object]) -> BookFocus | None:
    """A book's description and the chapters from the reader's own on. The current
    chapter is found by its volume and number as the SDK gives them, no parsing; None
    when ranobelib.me can't be reached (the panel then does without)."""
    entry = item["entry"]
    details = await title_details(entry.slug_url)  # type: ignore[attr-defined]
    if details is None:
        return None
    chapters = [chapter for volume in details.volumes for chapter in volume.chapters]
    current = next(
        (
            index
            for index, chapter in enumerate(chapters)
            if chapter.volume == entry.last_read_volume  # type: ignore[attr-defined]
            and chapter.number == entry.last_read_number  # type: ignore[attr-defined]
        ),
        None,
    )
    start = current or 0
    return BookFocus(
        summary=details.summary,
        status=details.status,
        position=current + 1 if current is not None else None,
        total=len(chapters),
        chapters=[
            BookChapter(
                volume=chapter.volume,
                number=chapter.number,
                name=chapter.name,
                is_current=index == current,
            )
            for index, chapter in enumerate(chapters[start : start + _NEXT_CHAPTERS], start=start)
        ],
    )


async def _home_dashboard(user: User | None) -> HomeDashboard | None:
    """Build the authenticated home dashboard from existing public service helpers."""
    if user is None:
        return None
    async with connection() as conn:
        library_items = await library_items_for_user(user, conn)
        reading = [
            item
            for item in library_items
            if item["entry"].last_read_at is not None  # type: ignore[union-attr]
        ][:_READING_LIMIT]
        return HomeDashboard(
            reading=reading,
            summary=await build_activity_summary(user, conn),
        )


@router.get("/continue", include_in_schema=False)
async def continue_reading(
    user: Annotated[User | None, Depends(get_current_user)],
) -> RedirectResponse:
    """PR 336: «Продолжить чтение» - the app icon's shortcut (manifest.webmanifest). The
    manifest is the same for everyone, so the personal part is here: a 302 to the chapter
    the home page's hero continues (the most recently read library entry, PR 306); for a
    guest or a library with nothing read yet, to the catalog. ``no-store``: where it
    leads changes with every chapter read, so no stored copy may ever answer it. Offline
    the service worker answers it from what's downloaded (app/pwa/service-worker.js)."""
    target = "/catalog"
    if user is not None:
        async with connection() as conn:
            entries = await list_entries(conn, user.id)
        entry = next((e for e in entries if e.last_read_volume is not None), None)
        if entry is not None:
            target = (
                f"/titles/{quote(entry.slug_url)}/chapters/"
                f"{quote(entry.last_read_volume)}/{quote(entry.last_read_number or '')}"
            )
    return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})


@router.post("/recent/{slug_url}/forget", response_model=None)
async def forget_recent_title(request: Request, slug_url: str) -> Response:
    """The "×" on a "Недавние" card (PR 69) - rewrites the cookie without this entry and
    returns 204 so recent-titles-forget.js can just drop the card from the page, no
    reload needed."""
    response = Response(status_code=204)
    forget(response, request, slug_url=slug_url)
    return response


async def _recent_with_progress(
    request: Request, user: User | None
) -> list[dict[str, str | int | None]]:
    """"Недавние" entries, each with a `progress_percent` alongside the usual `slug_url`/
    `name`/`cover_url` (see recent_titles.read_recent) - PR 68.

    Only computed for a logged-in user whose personal library (PR 14) has a matching
    `LibraryEntry` with recorded progress: an anonymous visitor has no `LibraryEntry` to
    match against at all, and a title only ever opened from the description page (never
    read) has no recorded position either. Neither case is a reason to start a parallel
    progress store on top of the one PR 27 already reads from `db/library.py`. conn is
    checked out here, not taken as a route-level Depends(get_connection) parameter, so an
    anonymous request never checks one out of the pool at all (see get_current_user()'s
    own docstring for the same reasoning).
    """
    recent = read_recent(request)
    if user is None:
        return [dict(item, progress_percent=None) for item in recent]
    result: list[dict[str, str | int | None]] = []
    async with connection() as conn:
        for item in recent:
            progress_percent = None
            entry = await get_entry(conn, user.id, item["slug_url"])
            if entry is not None and entry.last_read_volume is not None:
                try:
                    async with open_client(item["slug_url"]) as lib:
                        volumes = await lib.get_table_of_contents()
                    progress_percent = reading_progress_percent(
                        volumes, entry.last_read_volume, entry.last_read_number
                    )
                except RanobeLibError:
                    pass
            result.append(dict(item, progress_percent=progress_percent))
    return result

"""HTTP routes for private user-uploaded EPUB titles (issue 432).

Isolated from the external ranobelib.me catalog and SDK:
- Every route requires authentication (``require_current_user``) and verifies ownership via
  ``user_id``-scoped database queries. Another user's title ID always yields 404.
- Internal ``storage_key`` values and filesystem paths are never returned in HTML, JSON,
  URLs, or headers.
- Chapter reading reuses the site's existing web reader (``chapter.html``).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from psycopg import AsyncConnection
from pydantic import BaseModel

from app.auth.dependencies import require_current_user
from app.config import get_settings
from app.db.connection import get_connection
from app.db.user_titles import (
    UserTitle,
    UserTitleChapterSummary,
    get_active_title_file,
    get_chapter,
    get_title_asset,
    get_user_title,
    list_chapter_summaries,
    list_user_titles,
    record_user_title_read,
)
from app.db.users import User
from app.templating import templates
from app.user_titles.service import (
    UserTitleError,
    delete_user_title,
    edit_user_title,
    format_size_limit,
    import_epub,
)
from app.user_titles.storage import StorageNotFoundError, get_storage

router = APIRouter(prefix="/my-titles")


class _PatchUserTitleBody(BaseModel):
    title: str
    authors: list[str] | str | None = None
    description: str | None = None


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def _parse_uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=404, detail="Книга не найдена") from None


def _format_bytes(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ("Б", "КБ", "МБ"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ГБ"


def _content_disposition(filename: str) -> str:
    ascii_fallback = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in {'"', "\\", ";"} else "_" for ch in filename
    ).strip(" _")
    if not ascii_fallback:
        ascii_fallback = "book.epub"
    if not ascii_fallback.lower().endswith(".epub"):
        ascii_fallback += ".epub"
    utf8_quoted = quote(filename, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{utf8_quoted}'


def _title_public_dict(title: UserTitle) -> dict[str, object]:
    """Public JSON representation of a user title - never includes storage keys or paths."""
    return {
        "id": str(title.id),
        "title": title.title,
        "authors": title.authors,
        "description": title.description,
        "chapter_count": title.chapter_count,
        "has_cover": title.cover_storage_key is not None,
        "cover_url": f"/my-titles/{title.id}/cover" if title.cover_storage_key else None,
        "last_read_position": title.last_read_position,
        "url": f"/my-titles/{title.id}",
        "created_at": title.created_at,
        "updated_at": title.updated_at,
    }


def _my_titles_context(
    titles: list[UserTitle],
    *,
    upload_error: str | None = None,
    deleted: bool = False,
) -> dict[str, object]:
    max_bytes = get_settings().user_epub_max_size_bytes
    return {
        "active_nav": "my_titles",
        "titles": titles,
        "max_upload_bytes": max_bytes,
        "max_upload_label": format_size_limit(max_bytes),
        "upload_error": upload_error,
        "deleted": deleted,
    }


@router.get("", response_model=None)
async def show_my_titles(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    deleted: bool = False,
) -> Response:
    titles = await list_user_titles(conn, user.id)
    if _wants_json(request):
        return JSONResponse({"items": [_title_public_dict(t) for t in titles]})
    return templates.TemplateResponse(
        request,
        "my_titles.html",
        _my_titles_context(titles, deleted=deleted),
    )


@router.post("/upload", response_model=None)
async def upload_epub(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    file: Annotated[UploadFile, File()],
) -> Response:
    try:
        created = await import_epub(conn, user_id=user.id, upload=file)
    except UserTitleError as exc:
        if _wants_json(request):
            return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)
        titles = await list_user_titles(conn, user.id)
        return templates.TemplateResponse(
            request,
            "my_titles.html",
            _my_titles_context(titles, upload_error=str(exc)),
            status_code=exc.status_code,
        )

    if _wants_json(request):
        return JSONResponse(_title_public_dict(created), status_code=201)
    return RedirectResponse(url=f"/my-titles/{created.id}?uploaded=1", status_code=303)


def _user_title_page_context(
    title: UserTitle,
    chapters: list[UserTitleChapterSummary],
    file_info: dict[str, str] | None,
    *,
    uploaded: bool = False,
    updated: bool = False,
    finished: bool = False,
    edit_error: str | None = None,
) -> dict[str, object]:
    total = len(chapters)
    last_pos = title.last_read_position
    if last_pos is not None and not (1 <= last_pos <= total):
        last_pos = None
    progress_percent = round(last_pos * 100 / total) if last_pos and total else None
    current_chapter = chapters[last_pos - 1] if last_pos and total else None
    first_chapter = chapters[0] if chapters else None
    return {
        "active_nav": "my_titles",
        "strip_back_href": "/my-titles",
        "title": title,
        "chapters": chapters,
        "chapter_total": total,
        "first_chapter": first_chapter,
        "current_chapter": current_chapter,
        "last_read_position": last_pos,
        "progress_percent": progress_percent,
        "cover_url": f"/my-titles/{title.id}/cover" if title.cover_storage_key else None,
        "file_info": file_info,
        "uploaded": uploaded,
        "updated": updated,
        "finished": finished,
        "edit_error": edit_error,
    }


async def _public_file_info(
    conn: AsyncConnection, user_id: int, title_id: uuid.UUID
) -> dict[str, str] | None:
    active = await get_active_title_file(conn, user_id, title_id)
    if active is None:
        return None
    # Deliberately omits storage_key.
    return {
        "original_filename": active.original_filename,
        "size_label": _format_bytes(active.size_bytes),
        "import_status": active.import_status,
        "uploaded_at": active.uploaded_at,
    }


@router.get("/{title_id}", response_model=None)
async def show_user_title(
    request: Request,
    title_id: str,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    uploaded: bool = False,
    updated: bool = False,
    finished: bool = False,
) -> Response:
    parsed_id = _parse_uuid(title_id)
    title = await get_user_title(conn, user.id, parsed_id)
    if title is None:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    chapters = await list_chapter_summaries(conn, user.id, parsed_id)
    file_info = await _public_file_info(conn, user.id, parsed_id)
    if _wants_json(request):
        return JSONResponse(
            {
                **_title_public_dict(title),
                "file": file_info,
                "chapters": [
                    {
                        "position": ch.position,
                        "title": ch.title,
                        "url": f"/my-titles/{title.id}/chapters/{ch.position}",
                    }
                    for ch in chapters
                ],
            }
        )
    return templates.TemplateResponse(
        request,
        "user_title.html",
        _user_title_page_context(
            title,
            chapters,
            file_info,
            uploaded=uploaded,
            updated=updated,
            finished=finished,
        ),
    )


@router.post("/{title_id}/edit", response_model=None)
async def edit_user_title_form(
    request: Request,
    title_id: str,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    title: Annotated[str, Form()] = "",
    authors: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
) -> Response:
    parsed_id = _parse_uuid(title_id)
    existing = await get_user_title(conn, user.id, parsed_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    try:
        updated = await edit_user_title(
            conn,
            user_id=user.id,
            title_id=parsed_id,
            title=title,
            authors_raw=authors,
            description=description,
        )
    except UserTitleError as exc:
        if _wants_json(request):
            return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)
        chapters = await list_chapter_summaries(conn, user.id, parsed_id)
        file_info = await _public_file_info(conn, user.id, parsed_id)
        return templates.TemplateResponse(
            request,
            "user_title.html",
            _user_title_page_context(existing, chapters, file_info, edit_error=str(exc)),
            status_code=exc.status_code,
        )

    if updated is None:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    if _wants_json(request):
        return JSONResponse(_title_public_dict(updated))
    return RedirectResponse(url=f"/my-titles/{parsed_id}?updated=1", status_code=303)


@router.patch("/{title_id}", response_model=None)
async def patch_user_title_json(
    title_id: str,
    body: _PatchUserTitleBody,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> JSONResponse:
    parsed_id = _parse_uuid(title_id)
    try:
        updated = await edit_user_title(
            conn,
            user_id=user.id,
            title_id=parsed_id,
            title=body.title,
            authors_raw=body.authors,
            description=body.description,
        )
    except UserTitleError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)
    if updated is None:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    return JSONResponse(_title_public_dict(updated))


@router.post("/{title_id}/delete", response_model=None)
@router.delete("/{title_id}", response_model=None)
async def remove_user_title(
    request: Request,
    title_id: str,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> Response:
    parsed_id = _parse_uuid(title_id)
    result = await delete_user_title(conn, user_id=user.id, title_id=parsed_id)
    if not result.deleted:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    if request.method == "DELETE" or _wants_json(request):
        return JSONResponse(
            {
                "deleted": True,
                "storage_cleanup_partial": bool(result.failed_storage_keys),
            }
        )
    return RedirectResponse(url="/my-titles?deleted=1", status_code=303)


@router.get("/{title_id}/chapters/{position}", response_class=HTMLResponse)
async def read_user_title_chapter(
    request: Request,
    title_id: str,
    position: int,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> HTMLResponse:
    parsed_id = _parse_uuid(title_id)
    if position < 1:
        raise HTTPException(status_code=404, detail="Глава не найдена")
    title = await get_user_title(conn, user.id, parsed_id)
    if title is None:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    chapter = await get_chapter(conn, user.id, parsed_id, position)
    if chapter is None:
        raise HTTPException(status_code=404, detail="Глава не найдена")

    summaries = await list_chapter_summaries(conn, user.id, parsed_id)
    await record_user_title_read(conn, user.id, parsed_id, position)

    total = len(summaries)
    index = next((i for i, ch in enumerate(summaries) if ch.position == position), 0)
    prev_summary = summaries[index - 1] if index > 0 else None
    next_summary = summaries[index + 1] if index + 1 < total else None

    base_url = f"/my-titles/{title.id}"
    prev_url = f"{base_url}/chapters/{prev_summary.position}" if prev_summary else None
    next_url = f"{base_url}/chapters/{next_summary.position}" if next_summary else None

    # Adapter object matching what chapter.html reads from an SDK Chapter.
    reader_chapter = SimpleNamespace(
        volume="1",
        number=str(chapter.position),
        name=chapter.title,
        content=chapter.content,
        footnotes=[],
    )
    next_chapter_view = (
        SimpleNamespace(number=str(next_summary.position), name=next_summary.title)
        if next_summary
        else None
    )
    prev_chapter_view = (
        SimpleNamespace(number=str(prev_summary.position), name=prev_summary.title)
        if prev_summary
        else None
    )
    title_percent = round((index + 1) * 100 / total) if total else None

    return templates.TemplateResponse(
        request,
        "chapter.html",
        {
            "user_title_mode": True,
            "slug_url": f"user-title-{title.id}",
            "chapter": reader_chapter,
            "prev_url": prev_url,
            "next_url": next_url,
            "next_chapter": next_chapter_view,
            "prev_chapter": prev_chapter_view,
            "title_percent": title_percent,
            "is_last_chapter": index == total - 1,
            "volume_index": index + 1,
            "volume_total": total,
            "volume_chapters": [
                {
                    "number": str(ch.position),
                    "name": ch.title,
                    "url": f"{base_url}/chapters/{ch.position}",
                }
                for ch in summaries
            ],
            "title_name": title.title,
            "branch_id": None,
            "export_formats": [],
            "back_url": base_url,
            "toc_url": f"{base_url}#title-panel-toc",
            "finished_url": f"{base_url}?finished=1",
        },
    )


@router.get("/{title_id}/cover")
async def get_user_title_cover(
    title_id: str,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> FileResponse:
    parsed_id = _parse_uuid(title_id)
    title = await get_user_title(conn, user.id, parsed_id)
    if title is None or not title.cover_storage_key:
        raise HTTPException(status_code=404, detail="Обложка не найдена")
    try:
        path = get_storage().resolve_path(title.cover_storage_key)
    except StorageNotFoundError:
        raise HTTPException(status_code=404, detail="Обложка не найдена") from None
    return FileResponse(
        path,
        media_type=title.cover_media_type or "application/octet-stream",
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{title_id}/assets/{asset_id}")
async def get_user_title_asset(
    title_id: str,
    asset_id: str,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> FileResponse:
    parsed_title_id = _parse_uuid(title_id)
    parsed_asset_id = _parse_uuid(asset_id)
    asset = await get_title_asset(conn, user.id, parsed_title_id, parsed_asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Изображение не найдено")
    try:
        path = get_storage().resolve_path(asset.storage_key)
    except StorageNotFoundError:
        raise HTTPException(status_code=404, detail="Изображение не найдено") from None
    return FileResponse(
        path,
        media_type=asset.media_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{title_id}/download")
async def download_user_title_epub(
    title_id: str,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> FileResponse:
    parsed_id = _parse_uuid(title_id)
    file_row = await get_active_title_file(conn, user.id, parsed_id)
    if file_row is None or file_row.import_status != "ready":
        raise HTTPException(status_code=404, detail="Файл книги не найден")
    try:
        path = get_storage().resolve_path(file_row.storage_key)
    except StorageNotFoundError:
        raise HTTPException(status_code=404, detail="Файл книги не найден") from None
    return FileResponse(
        path,
        media_type="application/epub+zip",
        headers={
            "Content-Disposition": _content_disposition(file_row.original_filename),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )

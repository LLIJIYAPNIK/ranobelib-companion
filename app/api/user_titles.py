"""HTTP routes for user-uploaded EPUB titles (issues 432 and 434).

Isolated from the external ranobelib.me catalog and SDK:
- ``router`` (``/my-titles``): strictly authenticated owner-only workspace. Every read and
  mutation is scoped to ``user.id``; another user's title ID always yields 404, and
  anonymous visitors are redirected to ``/login``.
- ``community_router`` (``/community-titles``): separate public catalog & web reader for
  published (``visibility = 'published'``) user titles.
  - Access policy (explicit): published books (listing, detail card, chapters, cover, and
    chapter assets) are readable by both authenticated users and guests, matching the rest
    of the site's reading experience. Non-published books (``private`` or moderator
    ``hidden``) always return 404 to everyone on ``/community-titles``.
  - Reading progress is persisted per ``(user_id, title_id)`` for authenticated readers
    only; guests can read without saving server-side progress.
  - Downloading the original EPUB on ``/community-titles/{id}/download`` is disabled by
    default (404) unless the owner explicitly enabled ``allow_download`` on the published
    book (or the viewer is the owner).
- Internal ``storage_key`` values and filesystem paths are never returned in HTML, JSON,
  URLs, or headers.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from psycopg import AsyncConnection
from pydantic import BaseModel

from app.auth.dependencies import get_current_user, require_current_user
from app.config import get_settings
from app.db.connection import get_connection
from app.db.user_titles import (
    COMMUNITY_DEFAULT_SORT,
    COMMUNITY_SORTS,
    VISIBILITY_HIDDEN,
    VISIBILITY_PUBLISHED,
    UserTitle,
    UserTitleChapterSummary,
    get_active_title_file,
    get_chapter,
    get_published_active_title_file,
    get_published_chapter,
    get_published_title_asset,
    get_published_user_title,
    get_title_asset,
    get_user_title,
    list_chapter_summaries,
    list_published_chapter_summaries,
    list_published_user_titles,
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
    publish_user_title,
    unpublish_user_title,
    update_user_title_download_policy,
)
from app.user_titles.storage import StorageNotFoundError, get_storage

router = APIRouter(prefix="/my-titles")
community_router = APIRouter(prefix="/community-titles")

COMMUNITY_PAGE_SIZE = 24
_COMMUNITY_SORT_LABELS: dict[str, str] = {
    "newest": "Сначала новые",
    "title": "По названию",
    "author": "По автору",
}
_TRUTHY_FORM_VALUES = {"1", "true", "yes", "on"}


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


def _format_date_short(iso_timestamp: str | None) -> str | None:
    if not iso_timestamp:
        return None
    try:
        return datetime.fromisoformat(iso_timestamp).strftime("%d.%m.%Y")
    except ValueError:
        return None


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


def _parse_bool_flag(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        return raw != 0
    return str(raw or "").strip().lower() in _TRUTHY_FORM_VALUES


async def _parse_publish_payload(request: Request) -> tuple[bool, bool]:
    """Reads ``(confirm, allow_download)`` from either a JSON body or an HTML form."""
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("application/json"):
        try:
            body = await request.json()
        except Exception:
            return False, False
        if not isinstance(body, dict):
            return False, False
        return _parse_bool_flag(body.get("confirm")), _parse_bool_flag(body.get("allow_download"))
    form = await request.form()
    return _parse_bool_flag(form.get("confirm")), _parse_bool_flag(form.get("allow_download"))


def _rewrite_chapter_html_for_community(content: str, title_id: uuid.UUID) -> str:
    """Rewrites private ``/my-titles/{id}/...`` asset & chapter URLs embedded at import
    time to ``/community-titles/{id}/...`` so other readers and guests can load chapter
    images and follow internal chapter links without touching owner-only routes."""
    private_prefix = f"/my-titles/{title_id}/"
    public_prefix = f"/community-titles/{title_id}/"
    return content.replace(
        f'src="{private_prefix}assets/', f'src="{public_prefix}assets/'
    ).replace(f'href="{private_prefix}chapters/', f'href="{public_prefix}chapters/')


def _title_public_dict(title: UserTitle, *, public_view: bool = False) -> dict[str, object]:
    """Public JSON representation of a user title - never includes storage keys or paths."""
    base = f"/community-titles/{title.id}" if public_view else f"/my-titles/{title.id}"
    data: dict[str, object] = {
        "id": str(title.id),
        "title": title.title,
        "authors": title.authors,
        "description": title.description,
        "chapter_count": title.chapter_count,
        "has_cover": title.cover_storage_key is not None,
        "cover_url": f"{base}/cover" if title.cover_storage_key else None,
        "last_read_position": title.last_read_position,
        "url": base,
        "visibility": title.visibility,
        "published_at": title.published_at,
        "allow_download": title.allow_download,
        "created_at": title.created_at,
        "updated_at": title.updated_at,
    }
    if public_view:
        data["publisher"] = {
            "user_id": title.user_id,
            "name": title.owner_name or f"Пользователь #{title.user_id}",
            "profile_url": f"/profile/{title.user_id}",
        }
    else:
        data["public_url"] = (
            f"/community-titles/{title.id}" if title.visibility == VISIBILITY_PUBLISHED else None
        )
        data["hidden_reason"] = title.hidden_reason
        data["hidden_at"] = title.hidden_at
    return data


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
    published: bool = False,
    unpublished: bool = False,
    finished: bool = False,
    edit_error: str | None = None,
    publish_error: str | None = None,
) -> dict[str, object]:
    total = len(chapters)
    last_pos = title.last_read_position
    if last_pos is not None and not (1 <= last_pos <= total):
        last_pos = None
    progress_percent = round(last_pos * 100 / total) if last_pos and total else None
    current_chapter = chapters[last_pos - 1] if last_pos and total else None
    first_chapter = chapters[0] if chapters else None
    can_publish = (
        title.visibility != VISIBILITY_HIDDEN
        and total > 0
        and file_info is not None
        and file_info.get("import_status") == "ready"
    )
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
        "can_publish": can_publish,
        "published_date": _format_date_short(title.published_at),
        "uploaded": uploaded,
        "updated": updated,
        "published": published,
        "unpublished": unpublished,
        "finished": finished,
        "edit_error": edit_error,
        "publish_error": publish_error,
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


async def _published_file_info(
    conn: AsyncConnection, title_id: uuid.UUID
) -> dict[str, str] | None:
    active = await get_published_active_title_file(conn, title_id)
    if active is None:
        return None
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
    published: bool = False,
    unpublished: bool = False,
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
            published=published,
            unpublished=unpublished,
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


@router.post("/{title_id}/publish", response_model=None)
async def publish_user_title_route(
    request: Request,
    title_id: str,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> Response:
    parsed_id = _parse_uuid(title_id)
    existing = await get_user_title(conn, user.id, parsed_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Книга не найдена")

    async def _error_response(message: str, status_code: int) -> Response:
        if _wants_json(request):
            return JSONResponse({"detail": message}, status_code=status_code)
        chapters = await list_chapter_summaries(conn, user.id, parsed_id)
        file_info = await _public_file_info(conn, user.id, parsed_id)
        return templates.TemplateResponse(
            request,
            "user_title.html",
            _user_title_page_context(existing, chapters, file_info, publish_error=message),
            status_code=status_code,
        )

    if not user.is_email_verified:
        return await _error_response("Подтвердите email, чтобы публиковать книги", 403)

    confirmed, allow_download = await _parse_publish_payload(request)
    if not confirmed:
        return await _error_response(
            "Подтвердите, что книга станет доступна другим пользователям", 400
        )

    try:
        updated = await publish_user_title(
            conn, user_id=user.id, title_id=parsed_id, allow_download=allow_download
        )
    except UserTitleError as exc:
        return await _error_response(str(exc), exc.status_code)

    if updated is None:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    if _wants_json(request):
        return JSONResponse(_title_public_dict(updated))
    return RedirectResponse(url=f"/my-titles/{parsed_id}?published=1", status_code=303)


@router.post("/{title_id}/unpublish", response_model=None)
async def unpublish_user_title_route(
    request: Request,
    title_id: str,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> Response:
    parsed_id = _parse_uuid(title_id)
    existing = await get_user_title(conn, user.id, parsed_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    try:
        updated = await unpublish_user_title(conn, user_id=user.id, title_id=parsed_id)
    except UserTitleError as exc:
        if _wants_json(request):
            return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)
        chapters = await list_chapter_summaries(conn, user.id, parsed_id)
        file_info = await _public_file_info(conn, user.id, parsed_id)
        return templates.TemplateResponse(
            request,
            "user_title.html",
            _user_title_page_context(existing, chapters, file_info, publish_error=str(exc)),
            status_code=exc.status_code,
        )
    if updated is None:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    if _wants_json(request):
        return JSONResponse(_title_public_dict(updated))
    return RedirectResponse(url=f"/my-titles/{parsed_id}?unpublished=1", status_code=303)


@router.post("/{title_id}/download-policy", response_model=None)
async def update_download_policy_route(
    request: Request,
    title_id: str,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> Response:
    parsed_id = _parse_uuid(title_id)
    _, allow_download = await _parse_publish_payload(request)
    updated = await update_user_title_download_policy(
        conn, user_id=user.id, title_id=parsed_id, allow_download=allow_download
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    if _wants_json(request):
        return JSONResponse(_title_public_dict(updated))
    return RedirectResponse(url=f"/my-titles/{parsed_id}?updated=1", status_code=303)


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


def _render_reader_chapter(
    request: Request,
    *,
    title: UserTitle,
    chapter_position: int,
    chapter_title: str,
    chapter_content: str,
    summaries: list[UserTitleChapterSummary],
    base_url: str,
) -> HTMLResponse:
    total = len(summaries)
    index = next((i for i, ch in enumerate(summaries) if ch.position == chapter_position), 0)
    prev_summary = summaries[index - 1] if index > 0 else None
    next_summary = summaries[index + 1] if index + 1 < total else None

    prev_url = f"{base_url}/chapters/{prev_summary.position}" if prev_summary else None
    next_url = f"{base_url}/chapters/{next_summary.position}" if next_summary else None

    # Adapter object matching what chapter.html reads from an SDK Chapter.
    reader_chapter = SimpleNamespace(
        volume="1",
        number=str(chapter_position),
        name=chapter_title,
        content=chapter_content,
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

    return _render_reader_chapter(
        request,
        title=title,
        chapter_position=chapter.position,
        chapter_title=chapter.title,
        chapter_content=chapter.content,
        summaries=summaries,
        base_url=f"/my-titles/{title.id}",
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


# --- Public community titles (/community-titles, issue 434) -------------------------------


@community_router.get("", response_model=None)
async def show_community_titles(
    request: Request,
    viewer: Annotated[User | None, Depends(get_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    q: Annotated[str, Query(max_length=200)] = "",
    sort: Annotated[str, Query(max_length=20)] = COMMUNITY_DEFAULT_SORT,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
) -> Response:
    """Public directory of published user titles, isolated from external ranobelib catalog."""
    sort_key = sort if sort in COMMUNITY_SORTS else COMMUNITY_DEFAULT_SORT
    titles, total = await list_published_user_titles(
        conn,
        query=q,
        sort=sort_key,
        page=page,
        page_size=COMMUNITY_PAGE_SIZE,
        viewer_user_id=viewer.id if viewer is not None else None,
    )
    pages = max(1, math.ceil(total / COMMUNITY_PAGE_SIZE))
    if _wants_json(request):
        return JSONResponse(
            {
                "items": [_title_public_dict(t, public_view=True) for t in titles],
                "total": total,
                "page": page,
                "pages": pages,
                "query": q.strip(),
                "sort": sort_key,
            }
        )
    return templates.TemplateResponse(
        request,
        "community_titles.html",
        {
            "active_nav": "community_titles",
            "titles": titles,
            "total": total,
            "query": q.strip(),
            "sort": sort_key,
            "sort_options": list(_COMMUNITY_SORT_LABELS.items()),
            "page": page,
            "pages": pages,
            "has_prev": page > 1,
            "has_next": page < pages,
        },
    )


@community_router.get("/{title_id}", response_model=None)
async def show_community_title(
    request: Request,
    title_id: str,
    viewer: Annotated[User | None, Depends(get_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    finished: bool = False,
) -> Response:
    """Public detail page for a published user title. Non-published books return 404."""
    parsed_id = _parse_uuid(title_id)
    title = await get_published_user_title(
        conn, parsed_id, viewer_user_id=viewer.id if viewer is not None else None
    )
    if title is None:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    chapters = await list_published_chapter_summaries(conn, parsed_id)
    file_info = await _published_file_info(conn, parsed_id)
    is_owner = viewer is not None and viewer.id == title.user_id
    can_download = (
        file_info is not None
        and file_info["import_status"] == "ready"
        and (title.allow_download or is_owner)
    )
    can_report = viewer is not None and not is_owner

    if _wants_json(request):
        return JSONResponse(
            {
                **_title_public_dict(title, public_view=True),
                "can_download": can_download,
                "download_url": f"/community-titles/{title.id}/download"
                if can_download
                else None,
                "chapters": [
                    {
                        "position": ch.position,
                        "title": ch.title,
                        "url": f"/community-titles/{title.id}/chapters/{ch.position}",
                    }
                    for ch in chapters
                ],
            }
        )

    total = len(chapters)
    last_pos = title.last_read_position
    if last_pos is not None and not (1 <= last_pos <= total):
        last_pos = None
    progress_percent = round(last_pos * 100 / total) if last_pos and total else None
    current_chapter = chapters[last_pos - 1] if last_pos and total else None
    first_chapter = chapters[0] if chapters else None

    return templates.TemplateResponse(
        request,
        "community_title.html",
        {
            "active_nav": "community_titles",
            "strip_back_href": "/community-titles",
            "title": title,
            "chapters": chapters,
            "chapter_total": total,
            "first_chapter": first_chapter,
            "current_chapter": current_chapter,
            "last_read_position": last_pos,
            "progress_percent": progress_percent,
            "cover_url": f"/community-titles/{title.id}/cover"
            if title.cover_storage_key
            else None,
            "file_info": file_info,
            "is_owner": is_owner,
            "can_download": can_download,
            "can_report": can_report,
            "publisher_name": title.owner_name or f"Пользователь #{title.user_id}",
            "published_date": _format_date_short(title.published_at),
            "finished": finished,
        },
    )


@community_router.get("/{title_id}/chapters/{position}", response_class=HTMLResponse)
async def read_community_title_chapter(
    request: Request,
    title_id: str,
    position: int,
    viewer: Annotated[User | None, Depends(get_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> HTMLResponse:
    """Web reader for a published user title (open to authenticated readers and guests)."""
    parsed_id = _parse_uuid(title_id)
    if position < 1:
        raise HTTPException(status_code=404, detail="Глава не найдена")
    title = await get_published_user_title(
        conn, parsed_id, viewer_user_id=viewer.id if viewer is not None else None
    )
    if title is None:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    chapter = await get_published_chapter(conn, parsed_id, position)
    if chapter is None:
        raise HTTPException(status_code=404, detail="Глава не найдена")

    summaries = await list_published_chapter_summaries(conn, parsed_id)
    if viewer is not None:
        await record_user_title_read(conn, viewer.id, parsed_id, position)

    rewritten_content = _rewrite_chapter_html_for_community(chapter.content, title.id)
    response = _render_reader_chapter(
        request,
        title=title,
        chapter_position=chapter.position,
        chapter_title=chapter.title,
        chapter_content=rewritten_content,
        summaries=summaries,
        base_url=f"/community-titles/{title.id}",
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@community_router.get("/{title_id}/cover")
async def get_community_title_cover(
    title_id: str,
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> FileResponse:
    parsed_id = _parse_uuid(title_id)
    title = await get_published_user_title(conn, parsed_id)
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
            "Cache-Control": "private, no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@community_router.get("/{title_id}/assets/{asset_id}")
async def get_community_title_asset(
    title_id: str,
    asset_id: str,
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> FileResponse:
    parsed_title_id = _parse_uuid(title_id)
    parsed_asset_id = _parse_uuid(asset_id)
    asset = await get_published_title_asset(conn, parsed_title_id, parsed_asset_id)
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
            "Cache-Control": "private, no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@community_router.get("/{title_id}/download")
async def download_community_title_epub(
    title_id: str,
    viewer: Annotated[User | None, Depends(get_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> FileResponse:
    """Downloads a published book's source EPUB only if ``allow_download`` is enabled
    by the owner (or the viewer is the owner)."""
    parsed_id = _parse_uuid(title_id)
    title = await get_published_user_title(conn, parsed_id)
    if title is None:
        raise HTTPException(status_code=404, detail="Файл книги не найден")
    is_owner = viewer is not None and viewer.id == title.user_id
    if not title.allow_download and not is_owner:
        raise HTTPException(status_code=404, detail="Файл книги не найден")
    file_row = await get_published_active_title_file(conn, parsed_id)
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

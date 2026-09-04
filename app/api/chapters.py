"""Online chapter reading."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from psycopg import AsyncConnection
from ranobelib.models import Volume

from app.auth.dependencies import get_current_user, require_current_user
from app.comment_attachment import CommentAttachmentError, save_comment_attachment
from app.db.activity import record_chapter_read
from app.db.comment_reactions import (
    count_reactions_for_comment,
    toggle_comment_reaction,
)
from app.db.comment_reactions import (
    count_reactions_for_paragraph as count_comment_reactions_for_paragraph,
)
from app.db.comment_reactions import (
    user_reactions_for_paragraph as user_comment_reactions_for_paragraph,
)
from app.db.comments import (
    Comment,
    count_comments,
    count_comments_for_paragraph,
    create_comment,
    delete_comment,
    edit_comment,
    list_comments_for_paragraph,
)
from app.db.connection import connection, get_connection
from app.db.library import add_entry, record_progress
from app.db.notifications import notify_comment_reaction
from app.db.reactions import (
    ALLOWED_EMOJI,
    count_reactions,
    count_reactions_for_paragraph,
    toggle_reaction,
    user_reactions,
)
from app.db.users import User
from app.markdown_render import render_comment_body
from app.services.client import open_client
from app.services.exports import available_export_formats
from app.templating import templates

router = APIRouter(prefix="/titles/{slug_url}/chapters")


@router.get("/{volume}/{number}")
async def read_chapter(
    request: Request,
    slug_url: str,
    volume: int,
    number: str,
    current_user: Annotated[User | None, Depends(get_current_user)],
    branch_id: int | None = Query(default=None),
) -> HTMLResponse:
    async with open_client(slug_url) as lib:
        chapter = await lib.get_chapter(volume, number, branch_id=branch_id)
        volumes = await lib.get_table_of_contents()
    if current_user is not None:
        # PR 35: opening any chapter adds the title to the library if it isn't there
        # yet, same as clicking "Добавить в библиотеку" - add_entry() is idempotent
        # (INSERT OR IGNORE), so repeat opens don't duplicate the entry or touch its
        # added_at (see app/db/library.py). conn is checked out here, not taken as a
        # Depends(get_connection) parameter, so an anonymous request - this whole branch
        # - never checks a connection out of the pool at all (see get_current_user()'s
        # own docstring for the same reasoning).
        async with connection() as conn:
            await add_entry(conn, current_user.id, slug_url)
            await record_progress(conn, current_user.id, slug_url, str(volume), number)
            # Unlike record_progress, this always writes - it's an activity feed entry,
            # not a library-membership check (see app/db/activity.py).
            await record_chapter_read(conn, current_user.id, slug_url, str(volume), number)
    prev_url, next_url = _adjacent_chapter_urls(slug_url, volumes, str(volume), number)
    return templates.TemplateResponse(
        request,
        "chapter.html",
        {
            "slug_url": slug_url,
            "chapter": chapter,
            "prev_url": prev_url,
            "next_url": next_url,
            "branch_id": branch_id,
            "export_formats": available_export_formats(),
        },
    )


@router.get("/{volume}/{number}/reactions")
async def get_reactions(
    slug_url: str,
    volume: int,
    number: str,
    current_user: Annotated[User | None, Depends(get_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    branch_id: str = Query(default=""),
) -> JSONResponse:
    """Aggregated reaction counts for every paragraph in this chapter, plus - for a
    logged-in visitor - which single emoji is their own current pick per paragraph, so
    the picker in paragraph-menu.js can highlight it. Public (no login required) the same
    way the chapter's own text is: reading who reacted what needs no account, only adding
    a reaction does (see post_reaction below)."""
    counts = await count_reactions(conn, slug_url, str(volume), number, branch_id)
    mine = (
        await user_reactions(conn, current_user.id, slug_url, str(volume), number, branch_id)
        if current_user is not None
        else {}
    )
    return JSONResponse(
        {
            "counts": {str(index): counts[index] for index in counts},
            "mine": {str(index): emoji for index, emoji in mine.items()},
        }
    )


@router.post("/{volume}/{number}/reactions")
async def post_reaction(
    slug_url: str,
    volume: int,
    number: str,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    paragraph_index: Annotated[int, Form()],
    emoji: Annotated[str, Form()],
    branch_id: Annotated[str, Form()] = "",
) -> JSONResponse:
    """Toggles the logged-in visitor's reaction on one paragraph - the only bit of this
    feature that needs a session (see get_reactions above), hence require_current_user
    rather than the optional get_current_user."""
    if emoji not in ALLOWED_EMOJI:
        raise HTTPException(status_code=400, detail="Недопустимая реакция")
    mine = await toggle_reaction(
        conn, user.id, slug_url, str(volume), number, branch_id, paragraph_index, emoji
    )
    counts = await count_reactions_for_paragraph(
        conn, slug_url, str(volume), number, branch_id, paragraph_index
    )
    return JSONResponse({"paragraph_index": paragraph_index, "counts": counts, "mine": mine})


@router.get("/{volume}/{number}/comments/counts")
async def get_comment_counts(
    slug_url: str,
    volume: int,
    number: str,
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    branch_id: str = Query(default=""),
) -> JSONResponse:
    """Aggregated comment counts (replies included) for every paragraph in this chapter,
    one query for the whole page - what renders the "N комментариев ▾" toggle under a
    paragraph before the visitor has expanded anything. Public, same as get_reactions."""
    counts = await count_comments(conn, slug_url, str(volume), number, branch_id)
    return JSONResponse({"counts": {str(index): counts[index] for index in counts}})


@router.get("/{volume}/{number}/comments")
async def get_comments(
    slug_url: str,
    volume: int,
    number: str,
    current_user: Annotated[User | None, Depends(get_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    paragraph_index: int = Query(...),
    branch_id: str = Query(default=""),
) -> JSONResponse:
    """The full comment tree for one paragraph - fetched lazily, only once the visitor
    actually clicks that paragraph's "▾" (paragraph-menu.js), not bundled into
    get_comment_counts above."""
    return JSONResponse(
        await _comments_response(
            conn, slug_url, str(volume), number, branch_id, paragraph_index, current_user
        )
    )


@router.post("/{volume}/{number}/comments")
async def post_comment(
    slug_url: str,
    volume: int,
    number: str,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    paragraph_index: Annotated[int, Form()],
    body: Annotated[str, Form()] = "",
    branch_id: Annotated[str, Form()] = "",
    parent_comment_id: Annotated[int | None, Form()] = None,
    attachment: Annotated[UploadFile | None, File()] = None,
) -> JSONResponse:
    """Creates a top-level comment (from PR 131's "Комментировать" menu item) or a reply
    (from a comment's own "Ответить" button) - both go through create_comment(),
    distinguished only by whether parent_comment_id is set. Returns the same shape
    get_comments does, so the client can re-render a paragraph's comment section with one
    response either way instead of needing separate code paths.

    `attachment` (PR 151, generalizing PR 150's GIF-only field) is optional - present only
    when the composer's attachment picker staged a file, one button for image/video/GIF
    alike (app/comment_attachment.py sniffs which it actually is). Processed before
    create_comment() ever runs, so a rejected/failed upload never leaves a comment behind
    with a dangling attachment reference."""
    attachment_path: str | None = None
    attachment_kind: str | None = None
    if attachment is not None and attachment.filename:
        try:
            attachment_path, attachment_kind = await save_comment_attachment(attachment)
        except CommentAttachmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        await create_comment(
            conn,
            user.id,
            slug_url,
            str(volume),
            number,
            branch_id,
            paragraph_index,
            body,
            parent_comment_id,
            attachment_path,
            attachment_kind,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(
        await _comments_response(
            conn, slug_url, str(volume), number, branch_id, paragraph_index, user
        )
    )


@router.patch("/{volume}/{number}/comments/{comment_id}")
async def patch_comment(
    slug_url: str,
    volume: int,
    number: str,
    comment_id: int,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    body: Annotated[str, Form()] = "",
) -> JSONResponse:
    """Overwrites a comment's own body - "Изменить" on a comment's own
    .paragraph-comment__body (paragraph-menu.js), never anyone else's: edit_comment()
    scopes the UPDATE by user_id, so a 404 here means either the comment doesn't exist or
    it isn't this visitor's, indistinguishable on purpose (same reasoning as PR 170's
    notification mark-read/delete). slug_url/volume/number in the URL are kept only for
    symmetry with this router's other comment endpoints - the paragraph to re-render is
    looked up from the comment's own stored key below, not trusted from the client."""
    try:
        updated = await edit_comment(conn, comment_id, user.id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Комментарий не найден")
    return JSONResponse(await _comment_paragraph_response(conn, comment_id, user))


@router.delete("/{volume}/{number}/comments/{comment_id}")
async def delete_comment_route(
    slug_url: str,
    volume: int,
    number: str,
    comment_id: int,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
) -> JSONResponse:
    """Soft-deletes a comment - "Удалить" on a comment's own actions, same ownership/404
    shape as patch_comment above."""
    if not await delete_comment(conn, comment_id, user.id):
        raise HTTPException(status_code=404, detail="Комментарий не найден")
    return JSONResponse(await _comment_paragraph_response(conn, comment_id, user))


@router.post("/{volume}/{number}/comments/{comment_id}/reactions")
async def post_comment_reaction(
    slug_url: str,
    volume: int,
    number: str,
    comment_id: int,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    value: Annotated[int, Form()],
) -> JSONResponse:
    """Toggles the logged-in visitor's like/dislike on one comment - PR 155, the same
    toggle/switch semantics as post_reaction above, just keyed by comment_id (a real
    foreign key, see app/db/comment_reactions.py) instead of a paragraph position.
    slug_url/volume/number aren't used for the lookup itself (comment_id already names
    the row uniquely), only kept in the URL for symmetry with this router's other
    comment/reaction endpoints.

    PR 167: notifies the comment's author only when a reaction is actually *set* (`mine`
    is not None) - undoing one (clicking the same button again) isn't a new event worth
    telling anyone about. notify_comment_reaction() itself handles the "don't notify
    yourself" and dedupe-while-unread cases."""
    if value not in (1, -1):
        raise HTTPException(status_code=400, detail="Недопустимое значение реакции")
    try:
        mine = await toggle_comment_reaction(conn, user.id, comment_id, value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if mine is not None:
        await notify_comment_reaction(conn, comment_id, user.id)
    counts = await count_reactions_for_comment(conn, comment_id)
    return JSONResponse({"comment_id": comment_id, "counts": counts, "mine": mine})


async def _comments_response(
    conn: AsyncConnection,
    slug_url: str,
    volume: str,
    number: str,
    branch_id: str,
    paragraph_index: int,
    current_user: User | None = None,
) -> dict:
    comments = await list_comments_for_paragraph(
        conn, slug_url, volume, number, branch_id, paragraph_index
    )
    count = await count_comments_for_paragraph(
        conn, slug_url, volume, number, branch_id, paragraph_index
    )
    reaction_counts = await count_comment_reactions_for_paragraph(
        conn, slug_url, volume, number, branch_id, paragraph_index
    )
    my_reactions = (
        await user_comment_reactions_for_paragraph(
            conn, current_user.id, slug_url, volume, number, branch_id, paragraph_index
        )
        if current_user is not None
        else {}
    )
    return {
        "paragraph_index": paragraph_index,
        "count": count,
        "comments": [
            _comment_to_dict(comment, reaction_counts, my_reactions) for comment in comments
        ],
    }


async def _comment_paragraph_response(
    conn: AsyncConnection, comment_id: int, current_user: User
) -> dict:
    """Rebuilds the same shape _comments_response returns, for the one paragraph a just-
    edited/deleted comment belongs to - looked up from the comment's own stored key rather
    than trusting slug_url/volume/number/branch_id from the request, so a mismatched path
    can never be used to fetch another paragraph's thread."""
    cursor = await conn.execute(
        "SELECT slug_url, volume, number, branch_id, paragraph_index "
        "FROM comments WHERE id = %s",
        (comment_id,),
    )
    row = await cursor.fetchone()
    return await _comments_response(
        conn,
        row["slug_url"],
        row["volume"],
        row["number"],
        row["branch_id"],
        row["paragraph_index"],
        current_user,
    )


def _comment_to_dict(
    comment: Comment,
    reaction_counts: dict[int, dict[str, int]],
    my_reactions: dict[int, int],
) -> dict:
    return {
        "id": comment.id,
        "user_id": comment.user_id,
        "author": comment.author,
        "avatar_url": comment.avatar_url,
        "avatar_initials": comment.avatar_initials,
        "body": comment.body,
        # delete_comment() already blanks the stored body, but rendering straight from it
        # (rather than a hardcoded "" here) keeps this the single place body -> HTML ever
        # happens, same as every other comment.
        "body_html": render_comment_body(comment.body),
        "created_at": comment.created_at,
        "updated_at": comment.updated_at,
        "is_deleted": comment.is_deleted,
        "parent_comment_id": comment.parent_comment_id,
        "attachment_url": comment.attachment_url,
        "attachment_kind": comment.attachment_kind,
        "reactions": reaction_counts.get(comment.id, {"like": 0, "dislike": 0}),
        "my_reaction": my_reactions.get(comment.id),
        "replies": [
            _comment_to_dict(reply, reaction_counts, my_reactions) for reply in comment.replies
        ],
    }


def _adjacent_chapter_urls(
    slug_url: str, volumes: list[Volume], volume: str, number: str
) -> tuple[str | None, str | None]:
    """Previous/next chapter URLs across the whole title, in the SDK's own chapter order."""
    flat = [(vol.number, chapter.number) for vol in volumes for chapter in vol.chapters]
    try:
        index = flat.index((volume, number))
    except ValueError:
        return None, None
    prev_url = _chapter_url(slug_url, flat[index - 1]) if index > 0 else None
    next_url = _chapter_url(slug_url, flat[index + 1]) if index < len(flat) - 1 else None
    return prev_url, next_url


def _chapter_url(slug_url: str, key: tuple[str, str]) -> str:
    volume, number = key
    return f"/titles/{slug_url}/chapters/{volume}/{number}"

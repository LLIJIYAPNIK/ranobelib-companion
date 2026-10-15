"""«Комментарии» (PR 350): the list's columns, filters and rows and the drawer's context,
shaped for admin/comments.html from app/db/admin_comments.py. Presentation only.

The queue of reports («Модерация») is wave 41 - the site has no «Пожаловаться» yet, so
there's no «Жалобы» column or filter here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlencode

from app.admin_kit import Column, Filter, format_number
from app.admin_overview import format_when
from app.db.admin_comments import (
    DELETED,
    HIDDEN,
    PUBLISHED,
    REPLY,
    ROOT,
    STATUSES,
    CommentDetail,
    CommentRow,
)
from app.markdown_render import render_comment_body
from app.services.titles import TitleSummary

EXCERPT_LENGTH = 120

STATUS_LABELS = {PUBLISHED: "опубликован", HIDDEN: "скрыт", DELETED: "удалён"}
STATUS_TONES = {PUBLISHED: "success", HIDDEN: "warning", DELETED: "neutral"}
ATTACHMENT_LABELS = {"gif": "GIF", "image": "картинка", "video": "видео"}

COLUMNS = (
    Column("id", "id", "mono", locked=True),
    Column("text", "Комментарий", sortable=False, locked=True),
    Column("author", "Автор", sortable=False),
    Column("where", "Тайтл / глава", sortable=False),
    Column("created_at", "Дата", "muted"),
    Column("reactions", "Реакции", "num"),
    Column("status", "Статус", sortable=False),
    Column("actions", "Действия", "actions", sortable=False),
)


def parse_status(raw: str | None) -> str | None:
    return raw if raw in STATUSES else None


def parse_kind(raw: str | None) -> str | None:
    return raw if raw in (ROOT, REPLY) else None


def comment_filters(args: Mapping[str, object]) -> tuple[Filter, ...]:
    return (
        Filter(
            "status",
            "Статус",
            tuple((key, STATUS_LABELS[key]) for key in STATUSES),
            parse_status(str(args.get("status") or "")) or "",
        ),
        Filter(
            "kind",
            "Тип",
            ((ROOT, "комментарий к абзацу"), (REPLY, "ответ")),
            parse_kind(str(args.get("kind") or "")) or "",
        ),
    )


def excerpt(row: CommentRow, length: int = EXCERPT_LENGTH) -> str:
    """The list's text: the start of the raw body on one line - or what stands in for it
    when there's none (a deleted comment, a GIF with no caption)."""
    if row.status == DELETED:
        return "Комментарий удалён"
    text = " ".join(row.body.split())
    if len(text) > length:
        text = text[:length].rstrip() + "…"
    if not text and row.attachment_kind:
        return f"[{ATTACHMENT_LABELS.get(row.attachment_kind, 'вложение')}]"
    return text or "—"


def chapter_href(row: CommentRow) -> str:
    """The chapter on the site - the same link shape notifications use; there's no anchor
    for one paragraph."""
    href = f"/titles/{row.slug_url}/chapters/{row.volume}/{row.number}"
    if row.branch_id:
        href += f"?{urlencode({'branch_id': row.branch_id})}"
    return href


def chapter_label(row: CommentRow) -> str:
    return f"Том {row.volume} · Глава {row.number}"


@dataclass(frozen=True)
class CommentListRow:
    id: int
    text: str
    author: str
    author_href: str
    title: str
    chapter: str
    chapter_href: str
    created_at: str | None
    reactions: str
    status: str
    status_label: str
    status_tone: str
    is_reply: bool
    has_attachment: bool
    open_href: str


def _title_name(row: CommentRow, titles: Mapping[str, TitleSummary]) -> str:
    summary = titles.get(row.slug_url)
    return summary.name if summary else row.slug_url


def comment_rows(
    rows: list[CommentRow],
    titles: Mapping[str, TitleSummary],
    tz: str,
    args: Mapping[str, object],
) -> list[CommentListRow]:
    return [
        CommentListRow(
            id=row.id,
            text=excerpt(row),
            author=row.author,
            author_href=f"/admin/users?{urlencode({'user': row.user_id})}",
            title=_title_name(row, titles),
            chapter=chapter_label(row),
            chapter_href=chapter_href(row),
            created_at=format_when(row.created_at, tz),
            reactions=f"+{format_number(row.likes)} −{format_number(row.dislikes)}",
            status=row.status,
            status_label=STATUS_LABELS[row.status],
            status_tone=STATUS_TONES[row.status],
            is_reply=row.parent_comment_id is not None,
            has_attachment=bool(row.attachment_kind) and row.status != DELETED,
            open_href=comment_href(args, row.id),
        )
        for row in rows
    ]


def comment_href(args: Mapping[str, object], comment_id: int) -> str:
    """The list as it is (search, filters, sort, page) with this comment's drawer open."""
    kept = {key: value for key, value in args.items() if key != "comment" and value}
    return "/admin/comments?" + urlencode({**kept, "comment": comment_id})


def back_href(args: Mapping[str, object]) -> str:
    """Where an action returns to: the same list with the same drawer open."""
    query = urlencode({key: value for key, value in args.items() if value})
    return "/admin/comments" + (f"?{query}" if query else "")


def safe_back(raw: str | None) -> str:
    """An action's «back» field may only lead to the comments list - never another page
    or site (no open redirect through the form)."""
    if raw and (raw == "/admin/comments" or raw.startswith("/admin/comments?")) and "//" not in raw:
        return raw
    return "/admin/comments"


def without_drawer(back: str) -> str:
    """`back` with the drawer closed - after «Удалить навсегда» there may be nothing left
    to show in it."""
    path, _, query = back.partition("?")
    pairs = [pair for pair in query.split("&") if pair and not pair.startswith("comment=")]
    return path + (f"?{'&'.join(pairs)}" if pairs else "")


AUDIT_EXCERPT_LENGTH = 300


def comment_label(row: CommentRow) -> str:
    """The action log's name for a comment: «#42 · nick»."""
    return f"#{row.id} · {row.author}"


def comment_audit_details(row: CommentRow) -> dict[str, object]:
    """What the action log keeps about the comment an action touched: whose it was,
    where, and the start of its text - so a deleted comment's log entry still says
    what was removed."""
    return {
        "author_id": row.user_id,
        "author": row.author,
        "where": f"{row.slug_url} · {chapter_label(row)} · абзац {row.paragraph_index + 1}",
        "text": excerpt(row, AUDIT_EXCERPT_LENGTH),
    }


@dataclass(frozen=True)
class SummaryItem:
    label: str
    value: str


@dataclass(frozen=True)
class DrawerContext:
    """What the drawer shows: the comment in full (sanitized HTML, as readers see it), where
    it is, what it answers, and who wrote it."""

    id: int
    status: str
    status_label: str
    status_tone: str
    body_html: str
    attachment: str | None
    title: str
    chapter: str
    chapter_href: str
    paragraph: int  # 1-based, as a person counts
    items: list[SummaryItem]
    parent_author: str | None
    parent_text: str | None
    parent_href: str | None
    author: str
    author_href: str
    author_items: list[SummaryItem]
    replies: int


def drawer_context(
    detail: CommentDetail,
    titles: Mapping[str, TitleSummary],
    tz: str,
    args: Mapping[str, object],
    bans: int,
) -> DrawerContext:
    row = detail.comment
    deleted = row.status == DELETED
    items = [
        SummaryItem("Опубликован", format_when(row.created_at, tz) or "—"),
        SummaryItem("Изменён", format_when(row.updated_at, tz) or "нет"),
    ]
    if row.hidden_at and not deleted:
        items.append(SummaryItem("Скрыт", format_when(row.hidden_at, tz) or "—"))
    items.append(SummaryItem("Лайки / дизлайки", f"{row.likes} / {row.dislikes}"))
    items.append(SummaryItem("Ответов", format_number(detail.replies)))
    parent = detail.parent
    history = detail.author
    return DrawerContext(
        id=row.id,
        status=row.status,
        status_label=STATUS_LABELS[row.status],
        status_tone=STATUS_TONES[row.status],
        body_html="" if deleted else render_comment_body(row.body),
        attachment=ATTACHMENT_LABELS.get(row.attachment_kind or "", "вложение")
        if row.attachment_kind and not deleted
        else None,
        title=_title_name(row, titles),
        chapter=chapter_label(row),
        chapter_href=chapter_href(row),
        paragraph=row.paragraph_index + 1,
        items=items,
        parent_author=parent.author if parent else None,
        parent_text=excerpt(parent, 400) if parent else None,
        parent_href=comment_href(args, parent.id) if parent else None,
        author=row.author,
        author_href=f"/admin/users?{urlencode({'user': row.user_id})}",
        author_items=[
            SummaryItem("На сайте с", format_when(history.created_at, tz) or "—"),
            SummaryItem("Комментариев", format_number(history.comments)),
            SummaryItem("Из них скрыто", format_number(history.hidden)),
            SummaryItem("Блокировок", format_number(bans)),
        ],
        replies=detail.replies,
    )

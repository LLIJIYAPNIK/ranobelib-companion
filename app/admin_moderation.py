"""«Модерация» (PR 358): the queue's tabs and rows, shaped for admin/moderation.html from
app/db/admin_reports.py. Presentation only.

An object in the queue is addressed as ``comment-12`` / ``profile-7`` (the ``item`` query
parameter): the list shows the queue, the card on the right shows that object - or the
top of the queue when none is asked for.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from urllib.parse import urlencode

from app.admin_comments import (
    ATTACHMENT_LABELS,
    PARAGRAPH_QUOTE_LENGTH,
    chapter_href,
    chapter_label,
)
from app.admin_comments import excerpt as comment_excerpt
from app.admin_kit import format_number
from app.admin_overview import format_when
from app.auth.avatar import url_for
from app.db.admin_comments import DELETED, CommentDetail
from app.db.admin_reports import (
    COMMENT,
    PROFILE,
    TARGET_TYPES,
    AuthorRecord,
    ObjectReports,
    Queue,
    QueueItem,
    ReportedProfile,
)
from app.markdown_render import render_comment_body
from app.reports import reason_label
from app.services.titles import TitleSummary

KIND_LABELS = {COMMENT: "Комментарий", PROFILE: "Профиль"}
TAB_LABELS = {COMMENT: "Комментарии", PROFILE: "Профили"}
EXCERPT_LENGTH = 160
# From this many reports on one object its count turns red, as in the mockup.
MANY_REPORTS = 5


def parse_type(raw: str | None) -> str | None:
    return raw if raw in TARGET_TYPES else None


def parse_item(raw: str | None) -> tuple[str, int] | None:
    """``comment-12`` -> ("comment", 12); anything else -> None."""
    kind, _, number = (raw or "").partition("-")
    if kind in TARGET_TYPES and number.isdigit() and 0 < len(number) <= 10:
        value = int(number)
        if value <= 2_147_483_647:
            return kind, value
    return None


def item_key(target_type: str, target_id: int) -> str:
    return f"{target_type}-{target_id}"


def moderation_href(target_type: str | None = None, item: str | None = None) -> str:
    query = urlencode(
        {key: value for key, value in (("type", target_type), ("item", item)) if value}
    )
    return "/admin/moderation" + (f"?{query}" if query else "")


def safe_back(raw: str | None) -> str:
    """An action's «back» may only lead to the moderation screen (no open redirect)."""
    if (
        raw
        and (raw == "/admin/moderation" or raw.startswith("/admin/moderation?"))
        and "//" not in raw
    ):
        return raw
    return "/admin/moderation"


@dataclass(frozen=True)
class Tab:
    label: str
    href: str
    active: bool


def tabs(data: Queue, current: str | None) -> list[Tab]:
    total = sum(data.counts.values())
    result = [Tab(f"Все · {total}", moderation_href(), current is None)]
    for kind in TARGET_TYPES:
        result.append(
            Tab(
                f"{TAB_LABELS[kind]} · {data.counts.get(kind, 0)}",
                moderation_href(kind),
                current == kind,
            )
        )
    return result


def excerpt(text: str | None, length: int = EXCERPT_LENGTH) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= length else text[:length].rstrip() + "…"


@dataclass(frozen=True)
class QueueRow:
    key: str
    kind: str
    author: str
    when: str
    reports: int
    many: bool
    text: str
    reason: str
    href: str
    active: bool


def item_text(item: QueueItem) -> str:
    """What the queue shows of the object: the comment's text, the profile's «о себе» -
    or what stands in for it."""
    if item.author is None:
        return "Комментарий удалён навсегда" if item.target_type == COMMENT else "Аккаунт удалён"
    if item.deleted:
        return "Комментарий удалён автором"
    if item.target_type == PROFILE and not (item.text or "").strip():
        return "«О себе» не заполнено — жалоба на никнейм или аватар"
    return excerpt(item.text) or "[вложение без текста]"


def queue_rows(
    items: list[QueueItem], current_type: str | None, selected: str | None, tz: str
) -> list[QueueRow]:
    return [
        QueueRow(
            key=item_key(item.target_type, item.target_id),
            kind=KIND_LABELS[item.target_type],
            author=item.author or "—",
            when=format_when(item.first_at, tz) or "",
            reports=item.reports,
            many=item.reports >= MANY_REPORTS,
            text=item_text(item),
            reason=reason_label(item.top_reason),
            href=moderation_href(current_type, item_key(item.target_type, item.target_id)),
            active=selected == item_key(item.target_type, item.target_id),
        )
        for item in items
    ]


def page_args(args: Mapping[str, object]) -> dict[str, str]:
    return {key: str(value) for key, value in args.items() if key in ("type", "item") and value}


# --- the card -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ReasonBar:
    label: str
    count: int
    percent: int


@dataclass(frozen=True)
class Note:
    reason: str
    text: str
    when: str
    reporter: str
    reporter_href: str


@dataclass(frozen=True)
class HistoryItem:
    label: str
    value: str


@dataclass(frozen=True)
class Card:
    """What the card shows about one object. ``state`` is ``published``, ``hidden``
    (a comment hidden already), ``deleted`` (by its author) or ``gone`` (the comment or
    account no longer exists) - the last two can only be dismissed."""

    key: str
    target_type: str
    target_id: int
    kind: str
    reports: int
    since: str
    state: str
    where: str
    where_href: str | None
    author: str | None
    author_id: int | None
    author_href: str | None
    initial: str
    # A comment: its text as readers see it, the paragraph it's on, what it answers.
    body_html: str | None = None
    attachment: str | None = None
    paragraph: int | None = None
    paragraph_text: str | None = None
    parent_author: str | None = None
    parent_text: str | None = None
    # A profile: what can be cleared.
    nickname: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    reasons: list[ReasonBar] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    history: list[HistoryItem] = field(default_factory=list)
    ban: str | None = None  # the active block, in words


def reason_bars(reports: ObjectReports) -> list[ReasonBar]:
    total = max(reports.count, 1)
    return [
        ReasonBar(reason_label(item.reason), item.count, round(item.count * 100 / total))
        for item in reports.reasons
    ]


def notes(reports: ObjectReports, tz: str) -> list[Note]:
    return [
        Note(
            reason=reason_label(note.reason),
            text=note.details,
            when=format_when(note.created_at, tz) or "",
            reporter=note.reporter,
            reporter_href=f"/admin/users?{urlencode({'user': note.reporter_id})}",
        )
        for note in reports.notes
    ]


def history(record: AuthorRecord | None, bans: int, tz: str) -> list[HistoryItem]:
    if record is None:
        return []
    return [
        HistoryItem("На сайте с", format_when(record.created_at, tz) or "—"),
        HistoryItem("Комментариев", format_number(record.comments)),
        HistoryItem("Из них скрыто", format_number(record.hidden_comments)),
        HistoryItem(
            "Прошлые жалобы",
            f"{format_number(record.past_reports)} · подтверждено "
            f"{format_number(record.upheld_reports)}",
        ),
        HistoryItem("Блокировок", format_number(bans)),
    ]


def _initial(name: str | None) -> str:
    return (name or "?")[:1].upper()


def comment_card(
    detail: CommentDetail | None,
    comment_id: int,
    reports: ObjectReports,
    titles: Mapping[str, TitleSummary],
    paragraph: str | None,
    tz: str,
) -> Card:
    base = {
        "key": item_key(COMMENT, comment_id),
        "target_type": COMMENT,
        "target_id": comment_id,
        "kind": KIND_LABELS[COMMENT],
        "reports": reports.count,
        "since": format_when(reports.first_at, tz) or "",
        "reasons": reason_bars(reports),
        "notes": notes(reports, tz),
    }
    if detail is None:
        return Card(
            **base,
            state="gone",
            where="Комментарий удалён навсегда",
            where_href=None,
            author=None,
            author_id=None,
            author_href=None,
            initial="?",
        )
    row = detail.comment
    summary = titles.get(row.slug_url)
    title = summary.name if summary else row.slug_url
    parent = detail.parent
    return Card(
        **base,
        state=row.status,
        where=f"{title} · {chapter_label(row)} · абзац {row.paragraph_index + 1}",
        where_href=chapter_href(row),
        author=row.author,
        author_id=row.user_id,
        author_href=f"/admin/users?{urlencode({'user': row.user_id})}",
        initial=_initial(row.author),
        body_html=None if row.status == DELETED else render_comment_body(row.body),
        attachment=ATTACHMENT_LABELS.get(row.attachment_kind or "", "вложение")
        if row.attachment_kind and row.status != DELETED
        else None,
        paragraph=row.paragraph_index + 1,
        paragraph_text=excerpt(paragraph, PARAGRAPH_QUOTE_LENGTH)
        if paragraph is not None
        else None,
        parent_author=parent.author if parent else None,
        parent_text=comment_excerpt(parent, 400) if parent else None,
    )


def profile_card(
    profile: ReportedProfile | None, user_id: int, reports: ObjectReports, tz: str
) -> Card:
    base = {
        "key": item_key(PROFILE, user_id),
        "target_type": PROFILE,
        "target_id": user_id,
        "kind": KIND_LABELS[PROFILE],
        "reports": reports.count,
        "since": format_when(reports.first_at, tz) or "",
        "reasons": reason_bars(reports),
        "notes": notes(reports, tz),
    }
    if profile is None:
        return Card(
            **base,
            state="gone",
            where="Аккаунт удалён",
            where_href=None,
            author=None,
            author_id=None,
            author_href=None,
            initial="?",
        )
    name = profile.nickname or profile.email
    return Card(
        **base,
        state="published",
        where="Профиль пользователя",
        where_href=f"/profile/{profile.id}",
        author=name,
        author_id=profile.id,
        author_href=f"/admin/users?{urlencode({'user': profile.id})}",
        initial=_initial(name),
        nickname=profile.nickname,
        bio=(profile.bio or "").strip() or None,
        avatar_url=url_for(profile.avatar_path),
    )


def with_history(
    card: Card, record: AuthorRecord | None, bans: int, ban: str | None, tz: str
) -> Card:
    return replace(card, history=history(record, bans, tz), ban=ban)

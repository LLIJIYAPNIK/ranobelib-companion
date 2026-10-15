"""«Модерация» (PR 358): the queue's tabs and rows, shaped for admin/moderation.html from
app/db/admin_reports.py. Presentation only.

An object in the queue is addressed as ``comment-12`` / ``profile-7`` (the ``item`` query
parameter): the list shows the queue, the card on the right shows that object - or the
top of the queue when none is asked for.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlencode

from app.admin_overview import format_when
from app.db.admin_reports import COMMENT, PROFILE, TARGET_TYPES, Queue, QueueItem
from app.reports import reason_label

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

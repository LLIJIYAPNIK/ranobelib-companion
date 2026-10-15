"""«Журнал действий» (PR 353): the action log's entries (app/db/admin_audit.py, PR 343)
shaped for admin/audit.html and its CSV - who did what to which object, when on the
admin's clock, and the «− было / + стало» of each changed field. Presentation only.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from app.admin_kit import Filter
from app.admin_overview import format_when
from app.admin_tables import spreadsheet_safe
from app.db import admin_audit
from app.db.admin_audit import AuditEntry
from app.templating import plural

# Every action the panel records, in the «Действие» filter's order, with its badge tone.
ACTIONS: dict[str, tuple[str, str]] = {
    admin_audit.LOGIN: ("Вход", "neutral"),
    admin_audit.LOGIN_FAILED: ("Неудачный вход", "danger"),
    admin_audit.LOGOUT: ("Выход", "neutral"),
    admin_audit.USER_END_SESSIONS: ("Завершение сессий", "primary"),
    admin_audit.USER_CONFIRM_EMAIL: ("Подтверждение email", "success"),
    admin_audit.USER_SEND_PASSWORD_RESET: ("Сброс пароля", "primary"),
    admin_audit.USER_BAN: ("Блокировка", "danger"),
    admin_audit.USER_UNBAN: ("Снятие блокировки", "success"),
    admin_audit.COMMENT_HIDE: ("Скрытие", "warning"),
    admin_audit.COMMENT_SHOW: ("Восстановление", "success"),
    admin_audit.COMMENT_DELETE: ("Удаление", "danger"),
    admin_audit.REPORTS_DISMISS: ("Жалобы отклонены", "neutral"),
    admin_audit.REPORTS_HIDE_COMMENT: ("Скрытие по жалобам", "warning"),
    admin_audit.REPORTS_CLEAR_PROFILE: ("Очистка профиля", "warning"),
    admin_audit.REPORTS_BAN_AUTHOR: ("Блокировка по жалобам", "danger"),
    admin_audit.REPORTS_UNDO: ("Отмена решения", "primary"),
    admin_audit.DATA_EXPORT: ("Экспорт данных", "primary"),
    admin_audit.AUDIT_EXPORT: ("Экспорт журнала", "primary"),
}

ENTITIES = {
    "user": "Пользователь",
    "comment": "Комментарий",
    "profile": "Профиль",
    "table": "Таблица",
}

ACTORS = {admin_audit.ACTOR: "Администратор"}

# The names of the fields the actions record (app/api/admin.py); anything else shows as is.
FIELDS = {
    "hidden_at": "скрыт",
    "email_verified_at": "email подтверждён",
    "banned": "заблокирован",
    "expires_at": "до",
    "status": "статус",
    "effect": "что произошло",
    "sent_to": "письмо на",
    "term": "срок",
    "reason": "причина",
    "ban_id": "блокировка №",
    "author_id": "id автора",
    "author": "автор",
    "where": "где",
    "text": "текст",
    "replies": "ответов",
    "attachment_removed": "вложение удалено",
    "rows": "строк в файле",
    "matching": "подходило строк",
    "query": "поиск",
    "sort": "сортировка",
    "order": "порядок",
    "action": "действие",
    "actor": "кто",
    "locked_for_seconds": "вход закрыт, с",
    "reports": "жалоб",
    "report_ids": "жалобы №",
    "reasons": "причины",
    "bio": "о себе",
    "avatar_path": "аватар",
    "undoes": "отменяет запись №",
    "reopened": "вернулось в очередь",
}

# Values the actions write that read better in words.
_VALUES = {
    "removed": "удалён целиком",
    "deleted_kept_for_replies": "удалён, оставлен ради ответов",
    "deleted": "удалён",
    "asc": "по возрастанию",
    "desc": "по убыванию",
}


def parse_action(raw: str | None) -> str | None:
    return raw if raw in ACTIONS else None


def audit_filters(args: Mapping[str, object], actors: list[str]) -> tuple[Filter, ...]:
    """«Действие» always; «Кто» only once the log has more than one actor - with one
    password there's only ever «Администратор»."""
    filters = [
        Filter(
            "action",
            "Действие",
            tuple((key, label) for key, (label, _) in ACTIONS.items()),
            parse_action(str(args.get("action") or "")) or "",
        )
    ]
    if len(actors) > 1:
        chosen = str(args.get("actor") or "")
        filters.append(
            Filter(
                "actor",
                "Кто",
                tuple((actor, ACTORS.get(actor, actor)) for actor in actors),
                chosen if chosen in actors else "",
            )
        )
    return tuple(filters)


def format_value(value: Any, tz: str) -> str:
    """One recorded value as a person reads it: a UTC timestamp on the admin's clock,
    yes/no, a dash for nothing; lists and objects as compact JSON."""
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, str):
        if len(value) >= 19 and value[4] == "-" and value[10] == "T":
            return format_when(value, tz) or value
        return _VALUES.get(value, value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


@dataclass(frozen=True)
class DiffLine:
    field: str
    label: str
    before: str | None  # None: the field wasn't there before (a creation)
    after: str | None  # None: it isn't there after (a deletion)


@dataclass(frozen=True)
class Detail:
    field: str
    label: str
    value: str


def diff_lines(details: Mapping[str, Any], tz: str) -> list[DiffLine]:
    """``details``' before/after (admin_audit.diff()) as one line per changed field."""
    before = details.get("before") if isinstance(details.get("before"), dict) else {}
    after = details.get("after") if isinstance(details.get("after"), dict) else {}
    return [
        DiffLine(
            key,
            FIELDS.get(key, key),
            format_value(before[key], tz) if key in before else None,
            format_value(after[key], tz) if key in after else None,
        )
        for key in {**before, **after}
    ]


def detail_items(details: Mapping[str, Any], tz: str) -> list[Detail]:
    """Everything else an action recorded - a reason, a count, where a comment was."""
    return [
        Detail(key, FIELDS.get(key, key), format_value(value, tz))
        for key, value in details.items()
        if key not in ("before", "after")
    ]


def entity_href(entity: str | None, entity_id: str | None) -> str | None:
    """The object on its own screen, while that screen can show it."""
    if not entity_id:
        return None
    if entity == "user" and entity_id.isdigit():
        return f"/admin/users?{urlencode({'user': entity_id})}"
    if entity == "comment" and entity_id.isdigit():
        return f"/admin/comments?{urlencode({'comment': entity_id})}"
    if entity == "table":
        return f"/admin/tables/{entity_id}"
    return None


def _changes(count: int, has_details: bool) -> str:
    if count:
        return f"{count} {plural(count, 'поле', 'поля', 'полей')}"
    return "подробности" if has_details else "без изменений"


@dataclass(frozen=True)
class AuditRow:
    id: int
    time: str
    date: str
    when: str  # the full date and time, for the title and screen readers
    iso: str
    actor: str
    actor_initial: str
    action: str
    action_label: str
    action_tone: str
    entity: str
    name: str
    href: str | None
    ip: str
    changes: str
    diff: list[DiffLine]
    details: list[Detail]

    @property
    def expandable(self) -> bool:
        return bool(self.diff or self.details)


def _local(at: str, tz: str) -> datetime | None:
    try:
        return datetime.fromisoformat(at).astimezone(ZoneInfo(tz))
    except ValueError:
        return None


def audit_rows(entries: list[AuditEntry], tz: str, today: datetime) -> list[AuditRow]:
    rows = []
    for entry in entries:
        local = _local(entry.at, tz)
        label, tone = ACTIONS.get(entry.action, (entry.action, "neutral"))
        actor = ACTORS.get(entry.actor, entry.actor)
        diff = diff_lines(entry.details, tz)
        details = detail_items(entry.details, tz)
        when = format_when(entry.at, tz) or entry.at
        rows.append(
            AuditRow(
                id=entry.id,
                time=f"{local:%H:%M}" if local else "",
                date=("сегодня" if local.date() == today.date() else when.rsplit(",", 1)[0])
                if local
                else when,
                when=when,
                iso=local.isoformat(timespec="seconds") if local else entry.at,
                actor=actor,
                actor_initial=actor[:1].upper(),
                action=entry.action,
                action_label=label,
                action_tone=tone,
                entity=ENTITIES.get(entry.entity or "", entry.entity or ""),
                name=entry.entity_label or (f"#{entry.entity_id}" if entry.entity_id else ""),
                href=entity_href(entry.entity, entry.entity_id),
                ip=entry.ip or "—",
                changes=_changes(len(diff), bool(details)),
                diff=diff,
                details=details,
            )
        )
    return rows


# --- CSV ----------------------------------------------------------------------------------

CSV_HEADER = (
    "Время",
    "Кто",
    "Действие",
    "Объект",
    "id объекта",
    "Название",
    "IP",
    "Изменения",
    "Подробности",
)


def audit_csv(entries: list[AuditEntry], tz: str) -> bytes:
    """The log as UTF-8 CSV with a BOM (so Excel reads the Cyrillic right): the time on
    the admin's clock, the changes as «поле: было → стало», the rest of the details as
    «поле: значение». Formula-like cells arrive as text, as in «Данные БД»'s export."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_HEADER)
    for entry in entries:
        local = _local(entry.at, tz)
        changes = "; ".join(
            f"{line.label}: {line.before or ''} → {line.after or ''}"
            for line in diff_lines(entry.details, tz)
        )
        details = "; ".join(
            f"{item.label}: {item.value}" for item in detail_items(entry.details, tz)
        )
        writer.writerow(
            [
                spreadsheet_safe(cell)
                for cell in (
                    f"{local:%Y-%m-%d %H:%M:%S}" if local else entry.at,
                    ACTORS.get(entry.actor, entry.actor),
                    ACTIONS.get(entry.action, (entry.action, ""))[0],
                    ENTITIES.get(entry.entity or "", entry.entity or ""),
                    entry.entity_id or "",
                    entry.entity_label or "",
                    entry.ip or "",
                    changes,
                    details,
                )
            ]
        )
    return ("﻿" + out.getvalue()).encode("utf-8")


def csv_filename(now: datetime) -> str:
    return f"admin-audit-{now:%Y%m%d-%H%M}.csv"


def csv_href(args: Mapping[str, object]) -> str:
    """The export link for what the screen shows now: its search and filters, not its page."""
    kept = {key: args[key] for key in ("q", "action", "actor") if args.get(key)}
    query = urlencode(kept)
    return "/admin/audit/csv" + (f"?{query}" if query else "")

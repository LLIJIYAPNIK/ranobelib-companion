"""The admin panel's action log, ``admin_audit_log`` (migrations/0027_admin_audit_log.sql,
PR 343).

Append-only: this module only ever inserts, reads (the log screen, PR 353) and, for the
retention cleanup, deletes what is past its date - ``ADMIN_AUDIT_RETENTION_DAYS``, at
startup and daily. Admin routes that change data record what they did with
``record_change()``.

Nothing secret reaches ``details`` - every key that looks like a password, token, hash or
secret is replaced by MASK before the row is written, however deep it sits, using the
same rule the data browser uses to hide such columns (app/db/admin_data.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.db import connection as db_connection
from app.db.admin_data import MASK, _read_only, looks_secret

logger = logging.getLogger(__name__)

ACTOR = "admin"

# Kept short and plain: the log screen (PR 353) shows and filters by them.
LOGIN = "login"
LOGIN_FAILED = "login_failed"
LOGOUT = "logout"
# PR 348: «Пользователи»'s actions.
USER_END_SESSIONS = "user_end_sessions"
USER_CONFIRM_EMAIL = "user_confirm_email"
USER_SEND_PASSWORD_RESET = "user_send_password_reset"
# PR 349: blocking.
USER_BAN = "user_ban"
USER_UNBAN = "user_unban"
# PR 350: «Комментарии».
COMMENT_HIDE = "comment_hide"
COMMENT_SHOW = "comment_show"
COMMENT_DELETE = "comment_delete"
# PR 358: «Модерация»'s decisions on reports, and «Отменить» of one.
REPORTS_DISMISS = "reports_dismiss"
REPORTS_HIDE_COMMENT = "reports_hide_comment"
REPORTS_CLEAR_PROFILE = "reports_clear_profile"
REPORTS_BAN_AUTHOR = "reports_ban_author"
REPORTS_UNDO = "reports_undo"
MODERATION_DECISIONS = (
    REPORTS_DISMISS,
    REPORTS_HIDE_COMMENT,
    REPORTS_CLEAR_PROFILE,
    REPORTS_BAN_AUTHOR,
)
# PR 352: a CSV export from «Данные БД» - nothing changes, but data leaves the panel.
DATA_EXPORT = "data_export"
# PR 353: the same for the log's own CSV export.
AUDIT_EXPORT = "audit_export"

_dumps = partial(json.dumps, ensure_ascii=False, default=str)


@dataclass(frozen=True)
class AuditEntry:
    id: int
    at: str
    actor: str
    action: str
    entity: str | None
    entity_id: str | None
    entity_label: str | None
    ip: str | None
    details: dict[str, Any]


def scrub(value: Any) -> Any:
    """``value`` with every secret-looking key's value replaced by MASK - in nested dicts
    and lists too - so a caller passing a whole row or form can't leak one by accident."""
    if isinstance(value, dict):
        return {
            str(key): MASK if looks_secret(str(key)) else scrub(item) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub(item) for item in value]
    return value


async def record_admin_action(
    conn: AsyncConnection,
    action: str,
    *,
    ip: str | None,
    entity: str | None = None,
    entity_id: object = None,
    entity_label: str | None = None,
    details: dict[str, Any] | None = None,
) -> int:
    """Appends one entry and returns its id. ``at`` is now, in UTC, like every timestamp
    in the database; ``details`` is scrubbed of secrets first."""
    cursor = await conn.execute(
        "INSERT INTO admin_audit_log "
        "(at, actor, action, entity, entity_id, entity_label, ip, details) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            datetime.now(UTC).isoformat(),
            ACTOR,
            action,
            entity,
            None if entity_id is None else str(entity_id),
            entity_label,
            ip,
            Jsonb(scrub(details or {}), dumps=_dumps),
        ),
    )
    row = await cursor.fetchone()
    return row["id"]


async def list_entries(conn: AsyncConnection, limit: int = 100) -> list[AuditEntry]:
    """The newest entries first."""
    cursor = await conn.execute(
        "SELECT id, at, actor, action, entity, entity_id, entity_label, ip, details "
        "FROM admin_audit_log ORDER BY id DESC LIMIT %s",
        (limit,),
    )
    return [AuditEntry(**row) for row in await cursor.fetchall()]


async def get_entry(conn: AsyncConnection, entry_id: int) -> AuditEntry | None:
    """PR 358: one entry - «Модерация»'s «Отменить» undoes a decision from what its
    entry recorded, so nothing it restores passes through the browser."""
    cursor = await conn.execute(
        "SELECT id, at, actor, action, entity, entity_id, entity_label, ip, details "
        "FROM admin_audit_log WHERE id = %s",
        (entry_id,),
    )
    row = await cursor.fetchone()
    return AuditEntry(**row) if row else None


async def was_undone(conn: AsyncConnection, entry_id: int) -> bool:
    """Whether a REPORTS_UNDO entry already points at this one."""
    cursor = await conn.execute(
        "SELECT 1 FROM admin_audit_log WHERE action = %s AND details->>'undoes' = %s LIMIT 1",
        (REPORTS_UNDO, str(entry_id)),
    )
    return await cursor.fetchone() is not None


# --- the log screen (PR 353) ------------------------------------------------------------

PAGE_SIZE = 50
EXPORT_LIMIT = 10_000

_SELECT = "SELECT id, at, actor, action, entity, entity_id, entity_label, ip, details "


@dataclass(frozen=True)
class AuditPage:
    entries: list[AuditEntry]
    total: int
    page: int
    pages: int


@dataclass(frozen=True)
class AuditExport:
    entries: list[AuditEntry]
    total: int  # how many matched - more than len(entries) past EXPORT_LIMIT


def _where(query: str, action: str | None, actor: str | None) -> tuple[str, dict[str, object]]:
    """The screen's search - the object's name or id, or the IP (case-insensitive
    substring; position() so nothing in the text acts as a wildcard) - and filters."""
    conditions = []
    params: dict[str, object] = {}
    query = query.strip()
    if query:
        conditions.append(
            "(position(%(q)s in lower(coalesce(entity_label, ''))) > 0 "
            "OR position(%(q)s in lower(coalesce(entity_id, ''))) > 0 "
            "OR position(%(q)s in lower(coalesce(ip, ''))) > 0)"
        )
        params["q"] = query.lower()
    if action:
        conditions.append("action = %(action)s")
        params["action"] = action
    if actor:
        conditions.append("actor = %(actor)s")
        params["actor"] = actor
    return (f"WHERE {' AND '.join(conditions)} " if conditions else ""), params


async def list_page(
    conn: AsyncConnection,
    *,
    query: str = "",
    action: str | None = None,
    actor: str | None = None,
    page: int = 1,
) -> AuditPage:
    """One page of the log, newest first. Read-only with the admin timeout, like every
    other admin screen (app/db/admin_data.py)."""
    where, params = _where(query, action, actor)
    async with _read_only(conn):
        cursor = await conn.execute(f"SELECT COUNT(*) AS n FROM admin_audit_log {where}", params)
        total = (await cursor.fetchone())["n"]
        pages = max(1, math.ceil(total / PAGE_SIZE))
        page = min(max(page, 1), pages)
        cursor = await conn.execute(
            f"{_SELECT}FROM admin_audit_log {where}ORDER BY id DESC "
            "LIMIT %(limit)s OFFSET %(offset)s",
            {**params, "limit": PAGE_SIZE, "offset": (page - 1) * PAGE_SIZE},
        )
        rows = await cursor.fetchall()
    return AuditPage([AuditEntry(**row) for row in rows], total, page, pages)


async def export_entries(
    conn: AsyncConnection,
    *,
    query: str = "",
    action: str | None = None,
    actor: str | None = None,
) -> AuditExport:
    """The screen's current selection, every page up to EXPORT_LIMIT entries."""
    where, params = _where(query, action, actor)
    async with _read_only(conn):
        cursor = await conn.execute(f"SELECT COUNT(*) AS n FROM admin_audit_log {where}", params)
        total = (await cursor.fetchone())["n"]
        cursor = await conn.execute(
            f"{_SELECT}FROM admin_audit_log {where}ORDER BY id DESC LIMIT %(limit)s",
            {**params, "limit": EXPORT_LIMIT},
        )
        rows = await cursor.fetchall()
    return AuditExport([AuditEntry(**row) for row in rows], total)


async def actors(conn: AsyncConnection) -> list[str]:
    """Who appears in the log - just «admin» while the panel has one password; the
    screen's «Кто» filter shows up once there's more than one."""
    async with _read_only(conn):
        cursor = await conn.execute("SELECT DISTINCT actor FROM admin_audit_log ORDER BY actor")
        return [row["actor"] for row in await cursor.fetchall()]


# --- changes made by admin actions (PR 348-351)----------------------------------------


def diff(
    before: Mapping[str, Any] | None, after: Mapping[str, Any] | None
) -> dict[str, dict[str, Any]]:
    """``{"before": ..., "after": ...}`` holding only the fields that changed. Without a
    ``before`` it's a creation (all of ``after``), without an ``after`` a deletion (all
    of ``before``)."""
    if before is None:
        return {"before": {}, "after": dict(after or {})}
    if after is None:
        return {"before": dict(before), "after": {}}
    keys = [key for key in {**before, **after} if before.get(key) != after.get(key)]
    return {
        "before": {key: before.get(key) for key in keys},
        "after": {key: after.get(key) for key in keys},
    }


async def record_change(
    conn: AsyncConnection,
    action: str,
    *,
    ip: str | None,
    entity: str,
    entity_id: object,
    entity_label: str | None = None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> int:
    """What every admin route that changes data calls once it has: which entity, how it
    looked before and after (only the changed fields are kept, secrets masked), plus
    any ``extra`` context (a reason, a count). Call it on the same connection, after the
    change succeeded - a failed change leaves no entry.

        await record_change(conn, "user_block", ip=client_ip(request), entity="user",
                            entity_id=user.id, entity_label=user.nickname,
                            before={"blocked": False}, after={"blocked": True})
    """
    return await record_admin_action(
        conn,
        action,
        ip=ip,
        entity=entity,
        entity_id=entity_id,
        entity_label=entity_label,
        details={**diff(before, after), **(extra or {})},
    )


# --- retention ----------------------------------------------------------------------------

RETENTION_INTERVAL_SECONDS = 24 * 60 * 60


async def purge_expired(
    conn: AsyncConnection, retention_days: int, *, now: datetime | None = None
) -> int:
    """Deletes the entries older than ``retention_days`` and returns how many - the only
    delete the table's trigger lets through, and only inside this transaction."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.admin_audit_purge', 'on', true)")
        cursor = await conn.execute(
            "DELETE FROM admin_audit_log WHERE at::timestamptz < %s", (cutoff,)
        )
    return cursor.rowcount


async def retention_loop(retention_days: int) -> None:
    """Runs from app startup (app/main.py): a cleanup right away, then once a day for as
    long as the process lives. A failed round is logged and retried the next day."""
    while True:
        try:
            async with db_connection.connection() as conn:
                removed = await purge_expired(conn, retention_days)
            if removed:
                logger.info(
                    "Admin action log: removed %d entries older than %d days",
                    removed,
                    retention_days,
                )
        except Exception:
            logger.exception("Admin action log cleanup failed")
        await asyncio.sleep(RETENTION_INTERVAL_SECONDS)

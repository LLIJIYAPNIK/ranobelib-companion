"""The admin panel's action log, ``admin_audit_log`` (migrations/0027_admin_audit_log.sql,
PR 343).

Append-only: this module only ever inserts (and, for the retention cleanup, deletes what
is past its date). Nothing secret reaches ``details`` - every key that looks like a
password, token, hash or secret is replaced by MASK before the row is written, however
deep it sits, using the same rule the data browser uses to hide such columns
(app/db/admin_data.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.db.admin_data import MASK, looks_secret

ACTOR = "admin"

# Kept short and plain: the log screen (PR 353) shows and filters by them.
LOGIN = "login"
LOGIN_FAILED = "login_failed"
LOGOUT = "logout"

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

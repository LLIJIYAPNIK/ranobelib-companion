"""How a blocked account is told about it (PR 349) - the login refusal, the notice page
and the strip on every page share these words. Times are on the person's own clock
(users.timezone, UTC when unset), like the rest of what the site shows them."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.db.bans import Ban
from app.timezones import user_timezone

_MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)  # fmt: skip


def ban_until(ban: Ban, timezone: str | None) -> str:
    """«бессрочно» or «до 21 октября 2026, 14:30 (Europe/Moscow)»."""
    if ban.expires_at is None:
        return "бессрочно"
    tz = user_timezone(timezone)
    local = datetime.fromisoformat(ban.expires_at).astimezone(ZoneInfo(tz))
    month = _MONTHS_GENITIVE[local.month - 1]
    return f"до {local.day} {month} {local.year}, {local:%H:%M} ({tz})"


def ban_message(ban: Ban, timezone: str | None) -> str:
    """The one-line refusal: the term, and the reason when the admin gave one."""
    text = f"Аккаунт заблокирован {ban_until(ban, timezone)}."
    if ban.reason:
        text += f" Причина: {ban.reason}"
        if not ban.reason.endswith((".", "!", "?")):
            text += "."
    return text

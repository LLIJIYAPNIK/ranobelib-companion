"""When a queued reading event really happened (PR 332).

POST /reading-progress/tick and POST /activity/heartbeat can arrive late: read offline,
they wait in the device's queue (app/static/js/sync-queue.js) until the network is back.
The client sends how long ago the event happened (``age_ms``, measured on its own clock,
so a device clock that's off doesn't matter) rather than a timestamp; the server dates the
event from its own clock. A heartbeat then counts towards the day it was read on, and a
late tick doesn't pass for the newest position.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

EVENT_ID_PATTERN = r"^[A-Za-z0-9-]+$"
"""A queue event id (a UUID from crypto.randomUUID(), or the queue's own fallback)."""

MAX_EVENT_AGE_MS = 30 * 24 * 60 * 60 * 1000
"""How old a queued event may be. Older ones are refused (422) - the queue drops them
rather than backfilling a month-old reading session into the stats."""


def occurred_at(age_ms: int | None) -> str:
    """The event's UTC ISO timestamp, the format every ``created_at``/``last_read_at``
    uses. No age (a client from before this PR) means now."""
    return (datetime.now(UTC) - timedelta(milliseconds=age_ms or 0)).isoformat()

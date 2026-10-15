"""What a reader can report something for (PR 356) - the one list the API checks
against, the report form (PR 357) shows and the moderation screen (PR 358) counts by.

Keys are what ``reports.reason`` stores; the labels are only for display, so wording
can change without touching stored rows. The order is the form's order, «Другое» last.
"""

from __future__ import annotations

REPORT_REASONS: dict[str, str] = {
    "spam": "Спам или реклама",
    "abuse": "Оскорбления",
    "spoiler": "Спойлер без пометки",
    "copyright": "Нарушение авторских прав",
    "other": "Другое",
}

# The optional details a reporter can add, in characters.
DETAILS_LIMIT = 500

# New reports one reader can file in 24 hours - updating an open one doesn't count. Far
# above what an honest reader sends in a day, low enough that one account can't bury
# the moderation queue.
DAILY_LIMIT = 20


def reason_label(reason: str) -> str:
    """The reason's label, or the stored key itself for one no longer in the list."""
    return REPORT_REASONS.get(reason, reason)

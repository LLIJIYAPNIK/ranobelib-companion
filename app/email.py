"""Minimal outbound email - PR 225's "Забыли пароль?" flow is the first feature in this
repository that needs to send one at all (see CLAUDE.md's own note on why: a safe password
reset is impossible to verify without it). Deliberately not tied to any specific
transactional-email provider - configured via ``EMAIL_*`` (see app/config.py), any plain
SMTP-compatible provider works.

When ``EMAIL_HOST`` isn't configured (local dev, CI, or a deploy that hasn't set it up
yet), a send is logged instead of attempted, rather than raising - see DEPLOY.md for what
production is expected to set.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.config import get_settings

logger = logging.getLogger(__name__)


async def send_email(to: str, subject: str, body: str) -> None:
    """Sends a plain-text email off the event loop's own thread - ``smtplib`` is blocking
    network I/O, and this app's request path is otherwise fully async (see CLAUDE.md's
    stance on never wrapping blocking work in a thread pool for the SDK - same "don't
    stall the loop" reasoning, just for SMTP instead of the SDK)."""
    settings = get_settings()
    if not settings.email_host:
        logger.warning(
            "EMAIL_HOST not configured - logging email instead of sending (to=%s, subject=%s)",
            to,
            subject,
        )
        return
    await asyncio.to_thread(_send_sync, to, subject, body)


def _send_sync(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.email_from or settings.email_user or "no-reply@webnovells.ru"
    message["To"] = to
    message.set_content(body)

    with smtplib.SMTP(settings.email_host, settings.email_port) as smtp:
        smtp.starttls()
        if settings.email_user and settings.email_password:
            smtp.login(settings.email_user, settings.email_password)
        smtp.send_message(message)

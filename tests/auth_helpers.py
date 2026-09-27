"""Registering a logged-in test account now that registration needs email confirmation
(PR 246).

conftest.py's autouse `_capture_emails` fixture swaps app/api/auth.py's send_email for a
recorder writing to `sent_emails` below, so tests can read the confirmation code back out
of the "email" the same way a person would.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi.testclient import TestClient
from httpx import Response


@dataclass(frozen=True)
class SentEmail:
    to: str
    subject: str
    body: str


sent_emails: list[SentEmail] = []


async def record_email(to: str, subject: str, body: str) -> None:
    sent_emails.append(SentEmail(to=to, subject=subject, body=body))


def _latest_to(email: str) -> SentEmail:
    matches = [sent for sent in sent_emails if sent.to == email.strip().lower()]
    assert matches, f"no email was sent to {email}"
    return matches[-1]


def latest_verification_code(email: str) -> str:
    match = re.search(r"код на странице подтверждения: (\d{6})", _latest_to(email).body)
    assert match is not None
    return match.group(1)


def latest_verification_path(email: str) -> str:
    match = re.search(r"https?://[^/\s]+(/verify-email/\S+)", _latest_to(email).body)
    assert match is not None
    return match.group(1)


def register(
    client: TestClient,
    email: str = "alice@example.com",
    password: str = "hunter2pass",
    nickname: str | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    """POST /register, then confirm the email with the emailed code - leaves `client`
    logged in as the new account, like registering did before PR 246. Returns the
    response from the confirmation step (the avatar prompt, PR 106)."""
    data = {"email": email, "password": password, "password_confirm": password}
    if nickname is not None:
        data["nickname"] = nickname
    response = client.post("/register", data=data, headers=headers)
    # 303 for clients built with follow_redirects=False, 200 after following it.
    assert response.status_code in (200, 303), response.text
    return client.post("/verify-email", data={"code": latest_verification_code(email)})

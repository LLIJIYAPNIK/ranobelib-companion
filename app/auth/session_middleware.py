"""Session cookie with a configurable "remember me" lifetime (PR 36).

Starlette's own `SessionMiddleware` only supports one fixed `max_age` for every
session. This subclass keeps its signing/cookie mechanics (itsdangerous, httponly,
samesite - see CLAUDE.md, "Авторизация") entirely as-is, and only makes the lifetime
variable: `default_max_age` unless the session carries `REMEMBER_ME_KEY` (set by
app/api/auth.py's login route when the "Запомнить меня" checkbox was ticked), in
which case `remember_max_age` applies instead.

Reading a cookie tries the short `default_max_age` window first (the common case) and
only falls back to the long `remember_max_age` window for sessions that actually opted
in - a signature that only verifies under the long window but isn't flagged remembered
is exactly a normal session that has outlived its short lifetime, and is correctly
treated as expired rather than silently accepted.
"""

from __future__ import annotations

import json
from base64 import b64decode, b64encode
from typing import Literal

import itsdangerous
from itsdangerous.exc import BadSignature
from starlette.datastructures import MutableHeaders, Secret
from starlette.middleware.sessions import Session
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REMEMBER_ME_KEY = "_remember_me"


class RememberMeSessionMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        secret_key: str | Secret,
        *,
        default_max_age: int,
        remember_max_age: int,
        session_cookie: str = "session",
        path: str = "/",
        same_site: Literal["lax", "strict", "none"] = "lax",
        https_only: bool = False,
        domain: str | None = None,
    ) -> None:
        self.app = app
        self.signer = itsdangerous.TimestampSigner(str(secret_key))
        self.session_cookie = session_cookie
        self.default_max_age = default_max_age
        self.remember_max_age = remember_max_age
        self.path = path
        self.security_flags = "httponly; samesite=" + same_site
        if https_only:
            self.security_flags += "; secure"
        if domain is not None:
            self.security_flags += f"; domain={domain}"

    def _decode(self, data: bytes) -> dict[str, object] | None:
        try:
            raw = self.signer.unsign(data, max_age=self.default_max_age)
            return json.loads(b64decode(raw))
        except BadSignature:
            pass
        try:
            raw = self.signer.unsign(data, max_age=self.remember_max_age)
        except BadSignature:
            return None
        candidate = json.loads(b64decode(raw))
        return candidate if candidate.get(REMEMBER_ME_KEY) else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):  # pragma: no cover
            await self.app(scope, receive, send)
            return

        connection = HTTPConnection(scope)
        initial_session_was_empty = True

        if self.session_cookie in connection.cookies:
            data = connection.cookies[self.session_cookie].encode("utf-8")
            decoded = self._decode(data)
            if decoded is not None:
                scope["session"] = Session(decoded)
                initial_session_was_empty = False
            else:
                scope["session"] = Session()
        else:
            scope["session"] = Session()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                session: Session = scope["session"]
                headers = MutableHeaders(scope=message)
                if session.accessed:
                    headers.add_vary_header("Cookie")
                if session.modified and session:
                    max_age = (
                        self.remember_max_age
                        if session.get(REMEMBER_ME_KEY)
                        else self.default_max_age
                    )
                    data = b64encode(json.dumps(session).encode("utf-8"))
                    data = self.signer.sign(data)
                    header_value = (
                        "{session_cookie}={data}; path={path}; {max_age}{security_flags}"
                    ).format(
                        session_cookie=self.session_cookie,
                        data=data.decode("utf-8"),
                        path=self.path,
                        max_age=f"Max-Age={max_age}; " if max_age else "",
                        security_flags=self.security_flags,
                    )
                    headers.append("Set-Cookie", header_value)
                elif session.modified and not initial_session_was_empty:
                    header_value = (
                        "{session_cookie}={data}; path={path}; {expires}{security_flags}"
                    ).format(
                        session_cookie=self.session_cookie,
                        data="null",
                        path=self.path,
                        expires="expires=Thu, 01 Jan 1970 00:00:00 GMT; ",
                        security_flags=self.security_flags,
                    )
                    headers.append("Set-Cookie", header_value)
            await send(message)

        await self.app(scope, receive, send_wrapper)

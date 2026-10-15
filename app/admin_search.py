"""The admin panel's global search (PR 353) - the top bar's box (⌘/Ctrl-K): screens of the
panel, accounts, comments and database tables matching what was typed, grouped for the
drop-down (js/admin-search.js) and for the full results page (admin/search.html).
Presentation only; the account and comment lookups are app/db/admin_search.py.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from urllib.parse import urlencode

from app.db.admin_search import CommentHit, UserHit

# Below this a query matches too much to be useful - except digits, which are an id.
MIN_QUERY = 2
# Rows per group: in the drop-down, and on the results page.
SUGGEST_LIMIT = 5
PAGE_LIMIT = 20

EXCERPT_LENGTH = 90

# More words a screen answers to than its sidebar label - what an admin might type.
SCREEN_WORDS = {
    "overview": "обзор главная kpi метрики dashboard",
    "analytics": "аналитика тайтлы главы статистика analytics",
    "users": "пользователи аккаунты читатели блокировка users",
    "comments": "комментарии модерация скрыть comments",
    "moderation": "модерация жалобы очередь пожаловаться reports moderation",
    "tables": "данные бд база таблицы database tables",
    "system": "система сервер версии диск кеш system",
    "audit": "журнал действий аудит история лог audit log",
}
# Screens that aren't in the sidebar but can be opened from the search.
EXTRA_SCREENS = (
    ("Поведение читателей", "/admin/analytics/behaviour", "аналитика когда читают удержание"),
)


@dataclass(frozen=True)
class Hit:
    label: str
    href: str
    sub: str = ""


@dataclass(frozen=True)
class Group:
    key: str
    label: str
    hits: list[Hit] = field(default_factory=list)
    more: str | None = None  # the screen's own search with the same text


def normalize(query: str) -> str:
    return " ".join(query.split())


def searchable(query: str) -> bool:
    return len(query) >= MIN_QUERY or query.isdigit()


def _matches(query: str, *texts: str) -> bool:
    words = query.lower().split()
    haystack = " ".join(texts).lower()
    return all(word in haystack for word in words)


def screen_hits(query: str, nav: Iterable[object]) -> list[Hit]:
    """The panel's own screens - by label or one of their words."""
    hits = [
        Hit(item.label, item.href, item.group or "Админ-панель")
        for item in nav
        if item.href and _matches(query, item.label, SCREEN_WORDS.get(item.section, ""))
    ]
    hits += [
        Hit(label, href, "Аналитика")
        for label, href, words in EXTRA_SCREENS
        if _matches(query, label, words)
    ]
    return hits


def table_hits(query: str, tables: Iterable[str]) -> list[Hit]:
    """Database tables of «Данные БД» - the browser's whitelist, by name."""
    needle = query.lower().replace(" ", "_")
    return [Hit(name, f"/admin/tables/{name}", "Данные БД") for name in tables if needle in name]


def user_hits(users: list[UserHit]) -> list[Hit]:
    return [
        Hit(
            user.nickname or user.email,
            f"/admin/users?{urlencode({'user': user.id})}",
            f"{user.email} · id {user.id}" if user.nickname else f"id {user.id}",
        )
        for user in users
    ]


def _excerpt(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= EXCERPT_LENGTH else text[:EXCERPT_LENGTH].rstrip() + "…"


def comment_hits(comments: list[CommentHit]) -> list[Hit]:
    return [
        Hit(
            "Комментарий удалён"
            if comment.is_deleted
            else (_excerpt(comment.body) or "[вложение]"),
            f"/admin/comments?{urlencode({'comment': comment.id})}",
            f"#{comment.id} · {comment.author} · {comment.slug_url}",
        )
        for comment in comments
    ]


def groups(
    query: str,
    *,
    screens: list[Hit],
    users: list[Hit],
    comments: list[Hit],
    tables: list[Hit],
    limit: int,
) -> list[Group]:
    """The non-empty groups, in the order an admin most often wants them."""
    q = urlencode({"q": query})
    found = [
        Group("screens", "Экраны", screens[:limit]),
        Group("users", "Пользователи", users[:limit], f"/admin/users?{q}"),
        Group("comments", "Комментарии", comments[:limit], f"/admin/comments?{q}"),
        Group("tables", "Таблицы БД", tables[:limit]),
    ]
    return [group for group in found if group.hits]


def as_json(query: str, found: list[Group]) -> dict[str, object]:
    return {
        "query": query,
        "all": f"/admin/search?{urlencode({'q': query})}",
        "groups": [asdict(group) for group in found],
    }

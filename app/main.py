import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import (
    activity,
    auth,
    chapters,
    downloads,
    downloads_section,
    exports,
    health,
    home,
    images,
    library,
    notifications,
    profile,
    settings,
    titles,
)
from app.auth.dependencies import get_current_user
from app.auth.session_middleware import RememberMeSessionMiddleware
from app.config import get_settings
from app.db import connection as db_connection
from app.db.migrate import run_migrations
from app.exceptions import register_exception_handlers
from app.security_headers import install_security_headers

if sys.platform == "win32":
    # psycopg's async mode refuses to run on Windows' default ProactorEventLoop (see
    # app/db/connection.py) - must be set before uvicorn (or asyncio.run(), e.g. in tests)
    # creates its own event loop, so this has to happen at import time, here, rather than
    # inside lifespan() below. app/gif_video.py's ffmpeg subprocess call was rewritten
    # (PR 191's async follow-up) specifically so nothing else in the app still needs the
    # ProactorEventLoop this replaces.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await db_connection.open_pool()
    async with db_connection.connection() as conn:
        await run_migrations(conn)
    yield
    await db_connection.close_pool()


app = FastAPI(
    title="ranobelib-companion",
    lifespan=lifespan,
    # Runs get_current_user() for every request (not just routes that declare it
    # themselves) so app/templating.py's Jinja context processor - which can't itself
    # await a database call the way a FastAPI dependency can - always finds a resolved
    # current_user cached on request.state by the time a template renders.
    dependencies=[Depends(get_current_user)],
)
install_security_headers(app)
app.add_middleware(
    RememberMeSessionMiddleware,
    secret_key=get_settings().session_secret_key,
    default_max_age=get_settings().session_max_age,
    remember_max_age=get_settings().session_remember_max_age,
    session_cookie="session",
    same_site="lax",
    https_only=get_settings().is_production,
)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
# Uploaded avatars (PR 96) live outside app/static - user data, not an app asset, same
# reasoning as cache_dir/db_path being kept outside the source tree. StaticFiles requires
# the directory to exist at mount time, unlike app/static which ships with the repo.
get_settings().avatar_dir.mkdir(parents=True, exist_ok=True)
app.mount("/avatars", StaticFiles(directory=get_settings().avatar_dir), name="avatars")
# Converted GIF-as-video comment attachments (PR 150) - same "user data outside
# app/static, directory must exist at mount time" treatment as /avatars above.
get_settings().comment_attachment_dir.mkdir(parents=True, exist_ok=True)
app.mount(
    "/comment-attachments",
    StaticFiles(directory=get_settings().comment_attachment_dir),
    name="comment-attachments",
)
app.include_router(health.router)
app.include_router(activity.router)
app.include_router(home.router)
app.include_router(auth.router)
app.include_router(chapters.router)
app.include_router(exports.router)
app.include_router(downloads.router)
app.include_router(downloads_section.router)
app.include_router(images.router)
app.include_router(library.router)
app.include_router(notifications.router)
app.include_router(profile.router)
app.include_router(settings.router)
app.include_router(titles.router)
register_exception_handlers(app)

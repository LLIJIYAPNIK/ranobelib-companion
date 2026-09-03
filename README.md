# ranobelib-companion

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

A web UI for reading and downloading [ranobelib.me](https://ranobelib.me) light novels,
built on top of [`ranobelib-python-sdk`](https://pypi.org/project/ranobelib-python-sdk/).
This repo is a thin FastAPI/Jinja2 layer around that SDK - all API access, caching,
content parsing and epub/fb2/txt/pdf generation live there, not here.

## Features

- Read chapters online, with optional tap-to-read paging and adjustable reading speed
- Download a single chapter, a volume, or a whole title in the background with live
  progress, in whichever formats the SDK supports on the server (no hardcoded list)
- Personal library, reading activity/calendar, and download history for signed-in users
- Catalog browsing with filters (genres, tags, country, authors/artists/translators as
  the underlying SDK grows to support them)
- Paragraph-level reactions and comments, with notifications for replies/votes
- Own email/password accounts - unrelated to and never integrated with a ranobelib.me
  login

## Not affiliated with ranobelib.me

This is an independent, unofficial companion site. It talks to ranobelib.me only
through its public API (via the SDK), is strictly read-only, and does not implement or
emulate logging into a ranobelib.me account.

## Development

The app's own data (accounts, personal library, activity) lives in Postgres - start one
locally first, e.g.:

```
docker run -d --name ranobelib-db -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=ranobelib_companion -p 5432:5432 postgres:16-alpine
```

```
uv sync
uv run uvicorn app.main:app --reload
```

Every other setting falls back to a sane default under the project directory (see
`app/config.py`) - only `DATABASE_URL` needs a real server behind it, and its own default
(`postgresql://postgres:postgres@localhost:5432/ranobelib_companion`) already matches the
`docker run` command above. See `CLAUDE.md` for architecture/roadmap and `TASK.md` for the
full spec.

## Deploying

The app reads all configuration from environment variables (`app/config.py`). Set
`ENVIRONMENT=production` to enable production-only behavior; **`SESSION_SECRET_KEY`
becomes required** when it's set (the app refuses to start without it, instead of
falling back to a random per-process key that would log every visitor out on each
deploy/restart), and the session cookie gets the `Secure` flag (so this should stay
unset for a plain `http://localhost` deployment - it would otherwise stop the browser
from ever sending the cookie back).

| Variable | Required | Default | Notes |
|---|---|---|---|
| `ENVIRONMENT` | no | (unset = development) | Set to `production` for a real deploy - see above. |
| `SESSION_SECRET_KEY` | **yes, in production** | random per process | Signs the session cookie. Must stay stable across restarts, or every visitor gets logged out on each deploy. |
| `CACHE_DIR` | no | `.ranobelib_cache` | SDK's public response cache (title/chapter data), shared across all users. Point at a persistent volume - it's wiped on every container restart otherwise. |
| `CACHE_TTL_SECONDS` | no | 6 hours | How long a cached API response is trusted before being re-fetched. |
| `DATABASE_URL` | no | `postgresql://postgres:postgres@localhost:5432/ranobelib_companion` | This app's own Postgres database (accounts, personal library, activity) - separate from `CACHE_DIR`. A real deploy should point this at a managed/persistent Postgres instance, not a container's own ephemeral storage. |
| `AVATAR_DIR` | no | `.ranobelib_avatars` | Uploaded profile avatars. Needs the same persistent-volume treatment. |
| `COMMENT_ATTACHMENT_DIR` | no | `.ranobelib_comment_attachments` | Converted comment attachments (GIF → silent looping mp4). Needs the same persistent-volume treatment. |
| `SESSION_MAX_AGE_SECONDS` | no | 14 days | Session cookie lifetime for an ordinary login. |
| `SESSION_REMEMBER_MAX_AGE_SECONDS` | no | 90 days | Session cookie lifetime when "Запомнить меня" was checked. |
| `DOWNLOAD_FILE_TTL_SECONDS` | no | 30 minutes | Fallback cleanup window for an exported title's file if nobody comes back to download it. |

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

**On Windows**, use `uv run python run_windows.py` instead - `psycopg`'s async mode (see
`app/db/connection.py`) can't run under the `ProactorEventLoop` uvicorn's own CLI picks by
default on Windows, so `uvicorn app.main:app` alone fails to start there. Not needed on
Linux/macOS (where this app is actually deployed), and `run_windows.py` doesn't support
`--reload` - restart it by hand after an edit.

Every other setting falls back to a sane default under the project directory (see
`app/config.py`) - only `DATABASE_URL` needs a real server behind it, and its own default
(`postgresql://postgres:postgres@localhost:5432/ranobelib_companion`) already matches the
`docker run` command above. See `CLAUDE.md` for architecture/roadmap and `TASK.md` for the
full spec.

## Deploying

See [`DEPLOY.md`](DEPLOY.md) for the full list of configuration variables, server
prerequisites, DNS, and step-by-step first/repeat deploy instructions (Docker Compose,
nginx/TLS, the CD pipeline).

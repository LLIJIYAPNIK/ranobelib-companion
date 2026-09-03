# ranobelib-companion

Web UI for reading and downloading ranobe, built on top of
[`ranobelib-python-sdk`](https://pypi.org/project/ranobelib-python-sdk/). See `CLAUDE.md`
for architecture/roadmap and `TASK.md` for the full spec.

## Development

```
uv sync
uv run uvicorn app.main:app --reload
```

No environment variables are required for local development - unset settings fall back
to sane defaults under the project directory (see `app/config.py`).

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
| `DB_PATH` | no | `.ranobelib_companion.db` | This app's own SQLite database (accounts, personal library, activity) - separate from `CACHE_DIR`. Needs the same persistent-volume treatment. |
| `AVATAR_DIR` | no | `.ranobelib_avatars` | Uploaded profile avatars. Needs the same persistent-volume treatment. |
| `COMMENT_ATTACHMENT_DIR` | no | `.ranobelib_comment_attachments` | Converted comment attachments (GIF → silent looping mp4). Needs the same persistent-volume treatment. |
| `SESSION_MAX_AGE_SECONDS` | no | 14 days | Session cookie lifetime for an ordinary login. |
| `SESSION_REMEMBER_MAX_AGE_SECONDS` | no | 90 days | Session cookie lifetime when "Запомнить меня" was checked. |
| `DOWNLOAD_FILE_TTL_SECONDS` | no | 30 minutes | Fallback cleanup window for an exported title's file if nobody comes back to download it. |

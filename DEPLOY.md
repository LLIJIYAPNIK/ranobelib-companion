# Deploying

This covers taking `webnovells.ru` from a bare VPS to a running site, and keeping it
running afterwards. It assumes no prior context about the project beyond what's in
`README.md`.

Not covered here, and deliberately not done yet - discussed separately and postponed:
backups (Postgres data, avatars, comment attachments), SSH/VPS hardening beyond the
minimal `ufw` rules below (no fail2ban, no key-only SSH enforcement), Docker log
rotation, and uptime monitoring.

## Prerequisites on the server

- Docker Engine + the Docker Compose plugin (`docker compose version` works)
- `nginx`
- `certbot` with its nginx plugin (`python3-certbot-nginx` on Debian/Ubuntu) - installing
  the `certbot` package on Debian/Ubuntu also installs and enables the `certbot.timer`
  systemd timer that renews certificates automatically; nothing else to set up for
  renewal
- `ufw`
- `git`

## DNS

Point an A record for `webnovells.ru` at the server's IP before running `certbot` below -
it validates ownership of the domain over HTTP, so it fails if the domain doesn't resolve
to this server yet. This deployment only serves the bare domain; there is no separate
`www.` A record or redirect set up.

## Configuration

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

Local development also reads these same variables, but every one of them falls back to a
working default under the project directory except `DATABASE_URL` - see `README.md`'s
Development section.

## First deploy

1. Clone the repository on the server and `cd` into it.

2. Create `.env` from `.env.example` and fill in real values - at minimum
   `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` (matching the credentials encoded in
   `DATABASE_URL`), `ENVIRONMENT=production`, a generated `SESSION_SECRET_KEY` (e.g.
   `openssl rand -hex 32`), and `DATABASE_URL` pointing at the `db` service (e.g.
   `postgresql://postgres:<password>@db:5432/ranobelib_companion`). See the table above
   for the rest - their defaults are fine for a normal deploy.

   ```
   cp .env.example .env
   ```

3. Start the app and database:

   ```
   docker compose up -d --build
   ```

   The app applies its own database migrations on startup (`app/main.py`'s `lifespan()`
   calls `run_migrations()` every time it boots) - no separate migration step.

   This builds the image locally the first time. Once CD (below) has pushed at least one
   image to `ghcr.io/llijiyapnik/ranobelib-companion`, later `docker compose pull` calls
   will need to authenticate to it - a brand-new GHCR package is created **private** by
   default even though this repository is public. Either switch its visibility to Public
   (the package's own page on GitHub → Package settings → Change visibility), or run
   `docker login ghcr.io -u <github-username> -p <a PAT with read:packages>` once on the
   server - the login persists in its Docker credential store, no CI secret needed for
   this.

4. Install the nginx site config and get a certificate:

   ```
   sudo cp deploy/webnovells.ru.conf /etc/nginx/sites-available/webnovells.ru.conf
   sudo ln -s /etc/nginx/sites-available/webnovells.ru.conf /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl reload nginx
   sudo certbot --nginx -d webnovells.ru --redirect
   ```

   `certbot` edits the deployed copy of the config in place to add the 443 server block
   and the HTTP→HTTPS redirect - the tracked `deploy/webnovells.ru.conf` in the repo
   stays the pre-TLS bootstrap version on purpose (see the comment at its top).

5. Lock down the firewall:

   ```
   sudo ufw allow 22/tcp
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   sudo ufw enable
   ```

6. Verify: `https://webnovells.ru` loads, `http://webnovells.ru` redirects to `https://`,
   and `http://<server IP>:8000` does not respond at all (the app container only binds
   `127.0.0.1:8000`, per `docker-compose.yml`).

## Redeploying after changes

`.github/workflows/cd.yml` builds and pushes an image to GitHub Container Registry on
every push to `main` (i.e. every merged PR, after CI already passed on it) as
`ghcr.io/llijiyapnik/ranobelib-companion:latest`, using the workflow's own `GITHUB_TOKEN` -
no separate registry credential to provision. It then SSHes into the server and runs
`docker compose pull && docker compose up -d` in `~/ranobelib-companion` - an ordinary
merge to `main` deploys itself, no manual step needed. Migrations run automatically on
the app container's next startup, same as in the first deploy.

The workflow never sees or handles application secrets - it only tells the server to
pull the new image and restart; the server's own `.env`, already sitting there, supplies
everything the containers need. It also only pulls/restarts the **image** - a change to
`docker-compose.yml` itself needs a `git pull` on the server before the next
`docker compose up -d` picks it up.

It authenticates over SSH using three repository secrets, set under this repository's
**Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | Server hostname or IP |
| `DEPLOY_USER` | SSH user to deploy as |
| `DEPLOY_SSH_KEY` | Private key (PEM) for that user, authorized on the server |

To redeploy by hand instead (e.g. while debugging, or before those secrets exist):

```
git pull
docker compose up -d --build
```

# Deploying

Not part of the normal developer onboarding flow (see `README.md` for that) - this is
what a real production server needs, and how `.github/workflows/cd.yml` pushes to it.

## How it works

Every push to `main` (i.e. every merged PR, after CI already passed on it):

1. `cd.yml`'s `build-and-push` job builds the app image from `Dockerfile` and pushes it
   to GitHub Container Registry as `ghcr.io/lliyapnik/ranobelib-companion:latest`, using
   the workflow's own `GITHUB_TOKEN` - no separate registry credential to provision.
2. Its `deploy` job SSHes into the server and runs `docker compose pull && docker compose
   up -d` in `~/ranobelib-companion`. The workflow never sees or handles application
   secrets - it only tells the server to pull the new image and restart; the server's own
   `.env`, already sitting there, supplies everything the containers need.

## Server prerequisites (one-time setup)

- Docker and the Docker Compose plugin installed.
- A `~/ranobelib-companion` directory containing:
  - `docker-compose.yml` - the same file from this repo (PR 192). Copy it over, or clone
    the repo there and pull it manually to update it (the CD workflow only pulls/restarts
    the **image**, it doesn't touch this file - a change to `docker-compose.yml` itself
    needs a manual copy to the server before the next `docker compose up -d`).
  - `.env` - real values, created by hand on the server and never committed (already
    covered by the existing `.gitignore` from PR 192 - this file just spells out what
    goes in it). Same variables as `.env.example`, plus:
    - `ENVIRONMENT=production`
    - `SESSION_SECRET_KEY` - a real random secret (e.g. `openssl rand -hex 32`); the app
      refuses to start without one when `ENVIRONMENT=production`.
    - `DATABASE_URL` - the real production connection string.
- Since `ghcr.io/lliyapnik/ranobelib-companion` is a new package, its first push creates
  it as **private** by default even though this repository is public - `docker compose
  pull` on the server will fail to authenticate until either:
  - the package's visibility is switched to Public (GitHub -> the package's own page ->
    Package settings -> Change visibility), or
  - the server runs `docker login ghcr.io -u <github-username> -p <a PAT with
    read:packages>` once - the login persists in the server's own Docker credential
    store, no CI secret needed for this.

## GitHub Secrets required for CD

Set under this repository's **Settings -> Secrets and variables -> Actions**:

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | Server hostname or IP |
| `DEPLOY_USER` | SSH user to deploy as |
| `DEPLOY_SSH_KEY` | Private key (PEM) for that user, authorized on the server |

No registry secret is needed here - `GITHUB_TOKEN` (automatically provided to every
workflow run) already covers pushing to GHCR.

FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY . .

RUN uv sync --all-groups

EXPOSE 8000

# --forwarded-allow-ips must be "*", not "127.0.0.1": nginx (PR 195) connects to
# 127.0.0.1:8000 on the *host*, but docker-compose NATs that into this container, so
# uvicorn sees the docker bridge gateway as the peer address, never literally 127.0.0.1 -
# a literal-IP allowlist here never matches and X-Forwarded-Proto from nginx is silently
# ignored, making the app emit http:// URLs (via url_for) on a https:// page, which
# browsers block as mixed content. Trusting any peer is fine specifically because nothing
# other than nginx can reach this port at all: docker-compose.yml binds it to
# 127.0.0.1 only, and ufw (see DEPLOY.md) doesn't open 8000 externally.
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]

FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY . .

RUN uv sync --all-groups

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=127.0.0.1"]
